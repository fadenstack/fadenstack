"""A tool call's arguments in the types its schema declares."""

from __future__ import annotations

import math
import re
from typing import Any

_INTEGER = re.compile(r"[+-]?\d+")
_UNCHANGED = object()


def coerce_to_schema(
    arguments: dict[str, Any],
    schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """*arguments* with the values a model quoted put back to their declared type.

    Small models write a number as a string -- ``"25"`` for ``25`` -- and the
    MCP server validates against its schema and refuses the call: 82 of 121
    ``get-sum`` calls on the dev stack (2026-09-26). Only a top-level argument
    whose schema type is number, integer or boolean, and not string, is
    changed, and only when its text reads exactly as one.
    """
    properties = (schema or {}).get("properties")
    if not isinstance(properties, dict) or not isinstance(arguments, dict):
        return arguments
    coerced = dict(arguments)
    for name, value in arguments.items():
        spec = properties.get(name)
        if not isinstance(value, str) or not isinstance(spec, dict):
            continue
        declared = spec.get("type")
        kinds = declared if isinstance(declared, list) else [declared]
        if "string" in kinds:
            continue
        typed = _typed(value.strip(), kinds)
        if typed is not _UNCHANGED:
            coerced[name] = typed
    return coerced


def _typed(text: str, kinds: list[Any]) -> Any:
    """*text* as the first of *kinds* it reads as, or ``_UNCHANGED``."""
    if ("integer" in kinds or "number" in kinds) and _INTEGER.fullmatch(text):
        return int(text)
    if "number" in kinds:
        try:
            number = float(text)
        except ValueError:
            number = math.nan
        if math.isfinite(number):
            return number
    if "boolean" in kinds and text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return _UNCHANGED
