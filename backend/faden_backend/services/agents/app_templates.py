"""Agents for Fadenstack's own apps, ready to create: each app's definition as its repository ships it.

``templates/`` holds copies of the apps' ``contracts/*.json`` (faden-agent-chrome, faden-agent-msoffice):
the instructions, the host tool contract and the greeting, in the body ``POST /api/agents`` takes. When an app
brings more than its copy here, the agent's Apps tab shows the difference (``app_review``), so a copy that
falls behind costs a review, not a broken agent.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).with_name("templates")

#: The app each template is for, by file name.
APPS = {
    "browser-assistant": "Chrome",
    "word-assistant": "Microsoft Word",
    "excel-assistant": "Microsoft Excel",
    "powerpoint-assistant": "Microsoft PowerPoint",
    "outlook-assistant": "Microsoft Outlook",
}


@lru_cache(maxsize=1)
def templates() -> list[dict[str, Any]]:
    """Every template: its id, app, slug, name, description, number of host tools and definition."""
    found = []
    for path in sorted(_DIR.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        found.append({
            "id": path.stem,
            "app": APPS.get(path.stem, body.get("name", path.stem)),
            "slug": body["slug"],
            "name": body["name"],
            "description": body.get("description"),
            "tools": len((body.get("version") or {}).get("host_tool_contract") or []),
            "definition": body,
        })
    return found
