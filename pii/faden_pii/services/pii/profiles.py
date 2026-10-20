"""Immutable local artifact identity and reproducible runtime configuration."""

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

from faden_pii.services.pii.errors import PIIError

GLINER2_REVISION = "1cb4166094dc58fa8d836429f060d6c95f62b495"
ONNX_REVISION = "6cfcbd93d7fc6f5e1658f80afa38f73891b91693"
LABELS = {
    "person": "PERSON",
    "first_name": "PERSON",
    "last_name": "PERSON",
    "full_name": "PERSON",
    "address": "LOCATION",
    "city": "LOCATION",
}
LANGUAGES = ("en", "de", "fr", "es")


@dataclass(frozen=True)
class Profile:
    backend: str = "presidio"
    fingerprint: str = "presidio-v2"
    languages: tuple[str, ...] = ("en",)
    threshold: float = 0.35
    thresholds: dict[str, dict[str, float]] = field(default_factory=dict)
    calibrated: bool = False
    entity_profile: str = "standard"

    @property
    def id(self) -> str:
        name = {
            "presidio": "presidio-legacy",
            "gliner2": "gliner2-multilingual-pii",
            "gliner-onnx": "patronus-small-pii",
        }[self.backend]
        return name if self.entity_profile == "standard" else name + "-extended-v1"


def verify_manifest(root: Path, manifest_path: Path, backend: str) -> dict:
    """Verify every declared local file before the loader can use it."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        data.get("version") != 1
        or data.get("backend") != backend
        or not data.get("files")
    ):
        raise ValueError("Invalid local PII artifact manifest")
    if data.get("revision") != (
        GLINER2_REVISION if backend == "gliner2" else ONNX_REVISION
    ):
        raise ValueError("Unvalidated PII model revision")
    for name, expected in data["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid PII manifest file path")
        digest = hashlib.sha256()
        with (root / relative).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("PII model integrity check failed")
    # Loaders must not read unchecked configuration, tokenizer or model files.
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file()
        and p.resolve() != manifest_path.resolve()
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    }
    if actual - set(data["files"]):
        raise ValueError("Unchecked files in PII model directory")
    return data


def load_calibration(path: Path | None) -> dict:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("thresholds", {}), dict):
        raise PIIError("pii_invalid_calibration", 503)
    default = data.get("default_threshold", 0.35)
    if type(default) not in (int, float) or not 0 <= default <= 1:
        raise PIIError("pii_invalid_calibration", 503)
    for language, values in data.get("thresholds", {}).items():
        if (
            language not in LANGUAGES
            or not isinstance(values, dict)
            or any(
                type(v) not in (int, float) or not 0 <= v <= 1 for v in values.values()
            )
        ):
            raise PIIError("pii_invalid_calibration", 503)
    return data
