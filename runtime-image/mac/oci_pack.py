"""Pack a built Mac runtime into an OCI image used only as a carrier.

The image is FROM scratch with one layer holding ``runtime/`` (the relocatable
Python, the venv and ``RUNTIME-MANIFEST.json``). It has no entrypoint and no
command: it is never run as a container. The node agent fetches it through the
backend's ``/images/save`` and extracts the layer natively.

Platform: the config says ``linux/arm64``, not ``darwin/arm64``. Measured on
27 Nov 2026: Docker's classic image store refuses ``os=darwin`` on pull and
load ("image operating system "darwin" cannot be used on this platform"); only
the containerd store accepts it. A Linux backend has to be able to hold the
carrier, so the real target is stated in labels and annotations instead
(``org.fadenstack.runtime.os=darwin``, ``org.fadenstack.runtime.kind=native-carrier``),
the same approach as Docker's own vllm-metal carrier. The backend selects it only
through the bundle's native manifest with an explicit darwin/apple match, never
as a container image for a linux/arm64 node.

Output: an OCI image layout as a tar (``docker load``-able, pushable with
``crane push``/``oras``/``skopeo``), plus a JSON of the identities the bundle pins.

Usage: python oci_pack.py <stage-dir-containing-runtime/> <out.tar> <image-ref> <digests.json>
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

LABELS = {
    "org.fadenstack.runtime.os": "darwin",
    "org.fadenstack.runtime.arch": "arm64",
    "org.fadenstack.runtime.kind": "native-carrier",
    "org.opencontainers.image.source": "https://github.com/fadenstack/fadenstack",
    "org.opencontainers.image.title": "Fadenstack Mac runtime (vLLM-Metal)",
}
# A fixed timestamp: the same runtime tree packs to the same digests.
EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _write_layer(stage: Path, dest: Path) -> None:
    """Write an uncompressed, reproducible tar of ``stage/runtime`` to *dest* (sorted, fixed owner and time)."""
    root = stage / "runtime"
    if not (root / "RUNTIME-MANIFEST.json").is_file():
        raise SystemExit(f"{root}/RUNTIME-MANIFEST.json is missing; build the runtime first")
    with tarfile.open(dest, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in [root, *sorted(root.rglob("*"))]:
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(stage)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = EPOCH
            if info.isreg():
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
            else:
                tar.addfile(info)


def pack(stage: Path, out: Path, ref: str) -> dict[str, str | int]:
    work = out.parent / (out.name + ".work")
    work.mkdir(parents=True, exist_ok=True)
    layer_path = work / "layer.tar"
    gz_path = work / "layer.tar.gz"
    _write_layer(stage, layer_path)
    diff_id = _file_sha(layer_path)
    with layer_path.open("rb") as src, gz_path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as dst:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                dst.write(chunk)
    gz_digest = _file_sha(gz_path)
    gz_size = gz_path.stat().st_size
    unpacked = layer_path.stat().st_size
    layer_path.unlink()
    manifest_json = json.loads((stage / "runtime" / "RUNTIME-MANIFEST.json").read_text())
    labels = {**LABELS, "org.fadenstack.runtime.manifest": json.dumps(manifest_json.get("engines", {}), sort_keys=True)}
    config = json.dumps(
        {
            "architecture": "arm64",
            "os": "linux",
            "config": {"Labels": labels},
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
            "history": [{"created_by": "fadenstack runtime-image/mac/build_mac_runtime.sh"}],
        },
        sort_keys=True,
    ).encode()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": _sha(config), "size": len(config)},
            "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": gz_digest, "size": gz_size}],
            "annotations": {k: v for k, v in LABELS.items() if k.startswith("org.fadenstack.")},
        },
        sort_keys=True,
    ).encode()
    index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": _sha(manifest),
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": "arm64"},
                    "annotations": {"org.opencontainers.image.ref.name": ref.rsplit(":", 1)[-1], "io.containerd.image.name": ref},
                }
            ],
        }
    ).encode()
    with tarfile.open(out, "w") as tar:
        for name, data in (
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
            ("index.json", index),
            *((f"blobs/sha256/{_sha(b)[7:]}", b) for b in (config, manifest)),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = EPOCH
            tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(f"blobs/sha256/{gz_digest[7:]}")
        info.size = gz_size
        info.mtime = EPOCH
        with gz_path.open("rb") as fh:
            tar.addfile(info, fh)
    gz_path.unlink()
    work.rmdir()
    return {
        "image": ref,
        "manifest_digest": _sha(manifest),
        "config_digest": _sha(config),
        "layer_digest": diff_id,
        "compressed_layer_digest": gz_digest,
        "size_bytes": gz_size,
        "unpacked_size_bytes": unpacked,
    }


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    result = pack(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
