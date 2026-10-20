"""Versioned, approved local detector and locale identities."""

from importlib.resources import files
import json


def catalog() -> dict:
    return json.loads(
        files("faden_pii").joinpath("pii-catalog.json").read_text(encoding="utf-8")
    )


def selection(
    backend: str, model_id: str = "", locale: str = ""
) -> tuple[str, dict, str, dict]:
    data = catalog()
    model_id = model_id or next(
        key for key, entry in data["models"].items() if entry["adapter"] == backend
    )
    locale = locale or data["default_locale"]
    if model_id not in data["models"] or locale not in data["locales"]:
        raise ValueError("Unapproved PII model or locale pack")
    entry, pack = data["models"][model_id], data["locales"][locale]
    if entry["adapter"] != backend or not set(pack["languages"]) <= set(
        entry["languages"]
    ):
        raise ValueError("PII adapter and locale do not match the selected model")
    return model_id, entry, locale, pack
