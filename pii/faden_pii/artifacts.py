"""Provision local model files once; runtime loaders never access the hub."""

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import shutil

from faden_pii.services.pii.profiles import GLINER2_REVISION, ONNX_REVISION

MODELS = {
    "gliner2": (
        "fastino/gliner2-privacy-filter-PII-multi",
        GLINER2_REVISION,
        ["*.json", "*.safetensors", "*.model", "*.txt", "encoder_config/*", "LICENSE*"],
    ),
    "gliner-onnx": (
        "patronus-studio/gliner_small-v2.5-edge",
        ONNX_REVISION,
        ["*.json", "*.model", "onnx/fp16/*", "LICENSE*", "tokenizer*"],
    ),
}


def manifest(root: Path, backend: str, output: Path) -> dict:
    """Hash actual local files, including tokenizer and encoder configuration."""
    repo, revision, _ = MODELS[backend]
    files = {}
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.resolve() == output.resolve()
            or any(p.startswith(".") for p in path.relative_to(root).parts)
        ):
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files[path.relative_to(root).as_posix()] = digest.hexdigest()
    data = {
        "version": 1,
        "backend": backend,
        "repository": repo,
        "revision": revision,
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["provision", "manifest", "verify"])
    parser.add_argument("--backend", choices=list(MODELS), required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    output = args.manifest or args.model_root / "manifest.json"
    if args.operation == "provision":
        from huggingface_hub import snapshot_download

        repo, revision, patterns = MODELS[args.backend]
        source = Path(
            snapshot_download(repo, revision=revision, allow_patterns=patterns)
        )
        if args.model_root.exists() and any(args.model_root.iterdir()):
            raise ValueError(
                "Provision into a new model directory; retain the old version for rollback"
            )
        args.model_root.mkdir(parents=True, exist_ok=True)
        for path in source.rglob("*"):
            if path.is_file() and any(
                fnmatchcase(path.relative_to(source).as_posix(), pattern)
                for pattern in patterns
            ):
                target = args.model_root / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    if args.operation != "verify":
        manifest(args.model_root, args.backend, output)
    from faden_pii.services.pii.profiles import verify_manifest

    checked = verify_manifest(args.model_root, output, args.backend)
    print(
        json.dumps(
            {
                "backend": args.backend,
                "revision": checked["revision"],
                "verified_files": len(checked["files"]),
            }
        )
    )


if __name__ == "__main__":
    main()
