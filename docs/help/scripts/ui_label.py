"""What the console calls a thing in another language: for Help pages that name buttons and menus.

    python docs/help/scripts/ui_label.py "Create User" --lang de
    python docs/help/scripts/ui_label.py "Who can read it" "Knowledge Base" --lang de --contains

Looks the English text up in the console's texts (backend/i18n/en/*.json) and prints, for every key that
holds it, the text the same key has in the other language -- so a translated page names a button exactly as
the console shows it in that language. ``--contains`` also finds texts that contain the words.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

I18N = Path(__file__).resolve().parents[3] / "backend" / "i18n"


def _flat(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        elif isinstance(value, str):
            out[f"{prefix}{key}"] = value
    return out


def _texts(lang: str) -> dict[str, dict[str, str]]:
    return {f.name: _flat(json.loads(f.read_text(encoding="utf-8"))) for f in sorted((I18N / lang).glob("*.json"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="+", help="English text as the console shows it")
    ap.add_argument("--lang", default="de")
    ap.add_argument("--contains", action="store_true", help="also texts that contain it")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    english, other = _texts("en"), _texts(args.lang)
    for wanted in args.text:
        found: dict[str, list[str]] = {}
        for name, keys in english.items():
            for key, value in keys.items():
                hit = value.strip().lower() == wanted.strip().lower() or (
                    args.contains and wanted.strip().lower() in value.lower()
                )
                if hit and key in other.get(name, {}):
                    found.setdefault(other[name][key], []).append(f"{name}:{key}")
        print(f"{wanted!r}:")
        if not found:
            print("  (not in the console's texts)")
        for translated, where in sorted(found.items(), key=lambda kv: -len(kv[1]))[:8]:
            print(f"  {translated!r}  <- {', '.join(where[:3])}{' ...' if len(where) > 3 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
