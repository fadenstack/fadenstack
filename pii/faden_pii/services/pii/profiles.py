"""Immutable local artifact identity and reproducible runtime configuration."""

from dataclasses import dataclass, field
import hashlib
import json
from importlib.resources import files
from pathlib import Path

from faden_pii.services.pii.errors import PIIError
from faden_pii.catalog import catalog

GLINER2_REVISION = catalog()["models"]["gliner2-multi-v1"]["revision"]
ONNX_REVISION = catalog()["models"]["patronus-onnx-v1"]["revision"]
LABELS = catalog()["models"]["patronus-onnx-v1"]["labels"]
LANGUAGES = ("en", "de", "fr", "es")


def pipeline_sources() -> dict[str, str]:
    """Include local detection behavior in the profile/cache identity.

    Model bytes alone cannot distinguish changed rules, offset handling or
    post-processing. Normalize line endings so Windows and Linux source copies
    have the same identity; paths and installation location are excluded.
    """
    root = files("faden_pii.services.pii")
    sources = {
        name: hashlib.sha256(
            root.joinpath(name).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for name in (
            "profiles.py",
            "detectors.py",
            "extended.py",
            "extraction.py",
            "service.py",
            "spans.py",
            "windowing.py",
        )
    }
    sources["catalog.py"] = hashlib.sha256(
        files("faden_pii").joinpath("catalog.py").read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    sources["pii-catalog.json"] = hashlib.sha256(
        json.dumps(catalog(), sort_keys=True).encode()
    ).hexdigest()
    return sources


@dataclass(frozen=True)
class Profile:
    backend: str = "presidio"
    fingerprint: str = "presidio-v2"
    languages: tuple[str, ...] = ("en",)
    threshold: float = 0.35
    thresholds: dict[str, dict[str, float]] = field(default_factory=dict)
    calibrated: bool = False
    entity_profile: str = "standard"
    locale_pack: str = "europe-west-v1"

    @property
    def id(self) -> str:
        name = {
            "presidio": "presidio-legacy",
            "gliner2": "gliner2-multilingual-pii",
            "gliner-onnx": "patronus-small-pii",
        }[self.backend]
        name = name if self.entity_profile == "standard" else name + "-extended-v1"
        return (
            name
            if self.locale_pack == "europe-west-v1"
            else name + ":" + self.locale_pack
        )


def verify_manifest(root: Path, manifest_path: Path, backend: str) -> dict:
    """Verify every declared local file before the loader can use it."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        data.get("version") != 1
        or data.get("backend") != backend
        or not data.get("files")
    ):
        raise ValueError("Invalid local PII artifact manifest")
    entry = next(
        entry for entry in catalog()["models"].values() if entry["adapter"] == backend
    )
    if data.get("revision") != entry["revision"]:
        raise ValueError("Unvalidated PII model revision")
    for name, expected in data["files"].items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not (root / relative).resolve().is_relative_to(root.resolve())
        ):
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


def verify_calibration(data: dict, profile: Profile, revision: str) -> None:
    if data and (
        data.get("profile_id") != profile.id
        or data.get("model_revision") != revision
        or data.get("locale_pack") != profile.locale_pack
        or data.get("pipeline_sources") != pipeline_sources()
    ):
        raise ValueError(
            "Calibration does not match the pinned PII pipeline and locale"
        )
