"""Provision pinned PII artifacts without installing model libraries into the CLI."""

from __future__ import annotations

from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile

import httpx

from faden_cli.core.console import info

MODELS = {
    "gliner2": (
        "fastino/gliner2-privacy-filter-PII-multi",
        "1cb4166094dc58fa8d836429f060d6c95f62b495",
        ("*.json", "*.safetensors", "*.model", "*.txt", "encoder_config/*", "LICENSE*"),
    ),
    "gliner-onnx": (
        "patronus-studio/gliner_small-v2.5-edge",
        "6cfcbd93d7fc6f5e1658f80afa38f73891b91693",
        ("*.json", "*.model", "onnx/fp16/*", "LICENSE*", "tokenizer*"),
    ),
}


def provision(backend: str, root: Path) -> Path:
    """Download into a new directory atomically; reuse a manifest on later deploys.

    Inference remains offline. Only public model files, never request data, are
    exchanged here. A failed download cannot replace the previous installation.
    """
    if (root / "manifest.json").is_file():
        return root  # configure() verifies every declared checksum before use.
    if root.exists():
        raise ValueError(f"Unfinished/unverified PII model directory: {root}. Use a new directory.")
    repository, revision, patterns = MODELS[backend]
    root.parent.mkdir(parents=True, exist_ok=True)
    info(
        f"Provisioning pinned local PII model {repository}. Setup downloads model files; inference stays local."
    )
    with tempfile.TemporaryDirectory(prefix=".pii-download-", dir=root.parent) as temporary:
        stage = Path(temporary) / "model"
        stage.mkdir()
        files = {}
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=10)) as client:
            response = client.get(
                f"https://huggingface.co/api/models/{repository}/revision/{revision}",
                params={"blobs": "true"},
            )
            response.raise_for_status()
            metadata = response.json()
            if metadata.get("sha") != revision:
                raise ValueError("PII model repository returned an unexpected revision")
            for item in metadata["siblings"]:
                name = item["rfilename"]
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ValueError("Invalid PII artifact path")
                if not any(fnmatchcase(name, pattern) for pattern in patterns):
                    continue
                expected_size = item.get("size")
                if type(expected_size) is not int or not 0 <= expected_size <= 4 * 1024**3:
                    raise ValueError("Invalid PII artifact size")
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                sha256 = hashlib.sha256()
                git_sha = hashlib.sha1(f"blob {expected_size}\0".encode(), usedforsecurity=False)
                size = 0
                info(f"  Downloading {name} ({expected_size / 2**20:.1f} MiB)")
                with client.stream(
                    "GET", f"https://huggingface.co/{repository}/resolve/{revision}/{name}"
                ) as download:
                    download.raise_for_status()
                    with target.open("wb") as handle:
                        for chunk in download.iter_bytes():
                            size += len(chunk)
                            if size > expected_size:
                                raise ValueError("PII artifact exceeds expected size")
                            sha256.update(chunk)
                            git_sha.update(chunk)
                            handle.write(chunk)
                lfs = item.get("lfs")
                expected_digest = lfs.get("sha256") if isinstance(lfs, dict) else item.get("blobId")
                actual_digest = sha256.hexdigest() if lfs else git_sha.hexdigest()
                if size != expected_size or actual_digest != expected_digest:
                    raise ValueError("PII artifact checksum/size mismatch")
                files[name] = sha256.hexdigest()
        required = "model.safetensors" if backend == "gliner2" else "onnx/fp16/model_fp16.onnx"
        config = "config.json" if backend == "gliner2" else "gliner_config.json"
        if required not in files or config not in files:
            raise ValueError("PII model repository is missing required artifacts")
        (stage / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "backend": backend,
                    "repository": repository,
                    "revision": revision,
                    "files": files,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        stage.rename(root)
    return root
