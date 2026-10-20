"""Approved local detectors; the same immutable catalog ships in CLI and service."""

from importlib.resources import files
import json
from pathlib import Path


def catalog() -> dict:
    bundled = files("faden_cli").joinpath("bundle/pii-catalog.json")
    if bundled.is_file():
        return json.loads(bundled.read_text(encoding="utf-8"))
    source = Path(__file__).resolve().parents[4] / "pii/faden_pii/pii-catalog.json"
    return json.loads(source.read_text(encoding="utf-8"))


def model_for_backend(backend: str) -> str:
    for key, value in catalog()["models"].items():
        if value["adapter"] == backend:
            return key
    raise ValueError("Unapproved PII backend")


def resolve(model: str, locale: str, execution: str) -> tuple[str, dict, dict]:
    data = catalog()
    if locale not in data["locales"]:
        raise ValueError("Unsupported PII locale pack; no qualified adapter is installed for that language")
    pack = data["locales"][locale]
    model = pack["recommended_model"] if model == "auto" else model
    if model not in data["models"]:
        raise ValueError("Unknown PII model; choose an approved catalog ID")
    entry = data["models"][model]
    if not set(pack["languages"]) <= set(entry["languages"]):
        raise ValueError("PII model does not support the selected locale pack")
    if execution not in entry["execution"]:
        raise ValueError(f"PII model {model} does not support {execution}; select CPU or a compatible model")
    return model, entry, pack
