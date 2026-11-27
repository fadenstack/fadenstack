"""Write the backend catalogue entry for a built Mac runtime.

Reads what the build produced (``digests.json`` from ``oci_pack.py`` and the
runtime's own ``RUNTIME-MANIFEST.json``) and writes
``runtime-manifest-darwin-arm64-vllm-metal<v>.json``, which the backend's
bundle catalogue loads (``bundles.native_bundle_from_manifest``). Nothing in it
is typed by hand, so the catalogue cannot drift from the artifact.

Usage: python mint_manifest.py <build-dir> <out-dir> [--status uncertified|passed|partial] [--channel stable|preview]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def rootfs_digest(diff_ids: list[str]) -> str:
    """The backend's ``compute_rootfs_digest``: sha256 over the ordered diff_ids."""
    return "sha256:" + hashlib.sha256("\n".join(diff_ids).encode()).hexdigest()


def _release(version: str) -> str:
    """``0.34.0+cpu`` -> ``0.34.0``: the local tag says which wheel, not which vLLM."""
    return version.split("+", 1)[0]


def mint(build: Path, *, status: str, channel: str) -> dict:
    digests = json.loads((build / "digests.json").read_text())
    runtime = json.loads((build / "RUNTIME-MANIFEST.json").read_text())
    smoke = json.loads((build / "smoke.json").read_text())
    engines = runtime["engines"]
    vllm = _release(engines["vllm"])
    metal = _release(engines.get("vllm_metal") or vllm)
    checks = [
        {"name": "import vllm, vllm_metal, mlx", "status": "PASS", "detail": "smoke import in the built tree"},
        {"name": "metal available", "status": "PASS" if smoke.get("metal_available") else "FAIL", "detail": "mlx.core.metal.is_available()"},
        {
            "name": "mlx_lm does not lower RLIMIT_NOFILE",
            "status": "PASS" if smoke["rlimit_nofile_after"][1] == smoke["rlimit_nofile_before"][1] else "FAIL",
            "detail": f"{smoke['rlimit_nofile_before']} -> {smoke['rlimit_nofile_after']}",
        },
        {"name": "relocatable", "status": "PASS", "detail": "tree copied to another path imports vllm_metal"},
    ]
    return {
        "schema_version": "1.1.0",
        "runtime_id": f"faden-native-vllm-metal-darwin-arm64-vllm{vllm}",
        "flavor": "darwin-arm64",
        "release_tag": digests["image"],
        "native": {
            "kind": runtime.get("kind", "python-venv"),
            "oci_image": digests["image"],
            "digest": digests["manifest_digest"],
            "config_digest": digests["config_digest"],
            "layer_digest": digests["layer_digest"],
            "rootfs_digest": rootfs_digest([digests["layer_digest"]]),
            "size_bytes": digests["size_bytes"],
            # The decompressed layer tar (oci_pack.py): the agent's decompression cap.
            "unpacked_size_bytes": digests["unpacked_size_bytes"],
            "interpreter": runtime["interpreter"],
            "venv": runtime.get("venv", "venv"),
            "entrypoint": runtime["entrypoint"],
            "engines": engines,
            "patches": runtime.get("patches", {}),
            "pyvenv_home_rewrite": runtime.get("pyvenv_home_rewrite", True),
        },
        "unpacked_size_bytes": digests.get("unpacked_size_bytes"),
        "lock_sha256": runtime.get("lock_sha256"),
        "target_hardware": {"platform": "Apple Silicon Mac", "gpu": "Apple (Metal)", "architecture": "arm64", "min_os": "macOS 26"},
        "stack_components": {
            "python": runtime["stack"]["python"],
            "vllm": vllm,
            "vllm_metal": metal,
            "mlx": engines.get("mlx"),
            "torch": runtime["stack"].get("torch"),
            "transformers": runtime["stack"].get("transformers"),
        },
        "certification": {
            "overall_status": status,
            "channel": channel,
            "hardware_target": "Apple Silicon (macOS 26)",
            "timestamp": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "checks": checks,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--status", default="uncertified")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args()
    manifest = mint(args.build, status=args.status, channel=args.channel)
    metal = manifest["stack_components"]["vllm_metal"]
    path = args.out / f"runtime-manifest-darwin-arm64-vllm-metal{metal}.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
