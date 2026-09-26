"""The tools every chat has: one to find the others, one to read the clock.

A chat was offered every MCP tool it had not switched off, and nothing was
off until the user switched it off: GitHub's server alone brought 48 tools,
about 14.7k prompt tokens on every turn, needed or not (release VM,
2026-09-25). Now an MCP tool is off in a chat until it is switched on, and
the model gets two tools of the gateway's own:

- ``get_available_tools`` searches the MCP tools the chat may use, by what
  they do, and switches on the ones it finds: they are offered from the next
  round of the answer, and stay on for the rest of the chat, where the tool
  panel shows them.
- ``current_time`` reads the clock. The date is in the system prompt; the
  time is not, since a prompt that changes every minute is never reused from
  vLLM's cache.

The knowledge and attachment tools are the gateway's too, offered when there
is a knowledge base or a long file to search.
"""

from __future__ import annotations

import heapq
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from llm_port_api.services.gateway.attachment_search import words

FIND = "get_available_tools"
TIME = "current_time"
NAMES = frozenset({FIND, TIME})

#: Tools a search returns at most, whatever the model asks for.
_MAX_FOUND = 10
#: A tool's description as a search returns it; the whole one comes with the tool.
_DESCRIPTION_CHARS = 300
#: Names listed when a search finds nothing, so the model can search again.
_MAX_LISTED = 100
#: A tool found scores at least this share of the best match. Nearly every
#: GitHub tool mentions repositories: "search repositories" switched on
#: create_repository too.
_RELATIVE_FLOOR = 0.5

_FIND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": FIND,
        "description": (
            "Find tools for a task: searches the tools this chat can use -- "
            "services such as code hosting, tickets or files -- by what they do, "
            "and switches on the ones it finds, so they can be called from the "
            "next step on. Use it when no tool you have fits the task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What the tool should do, in a few words, e.g. 'search GitHub repositories'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many tools to switch on at most (1-{_MAX_FOUND}, default 5).",
                },
            },
            "required": ["query"],
        },
    },
}

_TIME_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TIME,
        "description": "The current date and time, in UTC or in a given time zone.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "An IANA time zone such as 'Europe/Berlin'; default UTC.",
                },
            },
        },
    },
}


def tools(*, find: bool) -> list[dict[str, Any]]:
    """The system tools to offer: the finder only when there is something to find."""
    return [_FIND_TOOL, _TIME_TOOL] if find else [_TIME_TOOL]


# ── get_available_tools ───────────────────────────────────────────

#: Where a tool's name splits into words: dots, "_", "-", and camelCase.
_NAME_BREAK = re.compile(r"[._\-\s]+|(?<=[a-z0-9])(?=[A-Z])")


def _name_words(name: str) -> str:
    return " ".join(_NAME_BREAK.split(name))


def _function(entry: dict[str, Any]) -> dict[str, Any]:
    return (entry.get("openai_tool") or {}).get("function") or {}


def _server(name: str) -> str:
    """The server part of ``mcp.<server>.<tool>``."""
    parts = name.split(".", 2)
    return parts[1] if len(parts) == 3 else ""


def _searched_text(entry: dict[str, Any]) -> str:
    """What a search matches a tool on: its server, its name twice, its description."""
    name = entry.get("qualified_name", "")
    tool = _name_words(name.split(".", 2)[-1])
    return f"{_server(name)} {tool} {tool} {_function(entry).get('description') or ''}"


def find(catalog: list[dict[str, Any]], arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """The tools of *catalog* that best match the call's query, best first.

    BM25 (k1 1.2, b 0.75) over each tool's server, name and description --
    a catalog is tens of tools, ranked in well under a millisecond -- keeping
    the tools that score at least half as well as the best.
    """
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError(f"{FIND} needs a query: what the tool should do.")
    try:
        limit = int(arguments.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, _MAX_FOUND))

    counted = [Counter(words(_searched_text(entry))) for entry in catalog]
    n = len(counted)
    if not n:
        return []
    lengths = [sum(c.values()) for c in counted]
    avgdl = max(sum(lengths) / n, 1.0)
    scores = [0.0] * n
    for term in set(words(_name_words(query))):
        df = sum(1 for c in counted if term in c)
        if not df:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for i, c in enumerate(counted):
            tf = c.get(term, 0)
            if tf:
                scores[i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[i] / avgdl))
    best = heapq.nlargest(limit, (i for i in range(n) if scores[i] > 0), key=lambda i: scores[i])
    floor = scores[best[0]] * _RELATIVE_FLOOR if best else 0.0
    return [catalog[i] for i in best if scores[i] >= floor]


#: Tools picked for a message at most.
_MAX_PICKED = 3


def picked_for(catalog: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The tools a message names: all the words of a tool's name, or two thirds and two at least.

    Picked before the model answers, so that it has the tool and its schema
    when the user names one ("use the get-sum tool", "search my
    repositories"). The 0.5B model on the dev stack called tools it had not
    been given, by names it made up and with arguments it guessed, and gave
    up at the first error rather than search. Chat that names no tool picks
    none: a single word shared with a longer name ("list the risks") is not
    enough.
    """
    said = set(words(_name_words(text)))
    if not said:
        return []
    scored: list[tuple[float, int, int]] = []
    for at, entry in enumerate(catalog):
        named = set(words(_name_words(entry.get("qualified_name", "").split(".", 2)[-1])))
        hit = len(named & said)
        if named and (hit == len(named) or (hit >= 2 and hit * 3 >= len(named) * 2)):
            scored.append((hit / len(named), hit, at))
    best = sorted(scored, key=lambda s: (-s[0], -s[1], s[2]))[:_MAX_PICKED]
    return [catalog[at] for _, _, at in best]


def found_answer(query: str, found: list[dict[str, Any]], catalog: list[dict[str, Any]]) -> str:
    """What the model is told a search found, as JSON."""
    if found:
        return json.dumps({
            "query": query,
            "switched_on": [
                {
                    "name": entry.get("qualified_name", ""),
                    "description": (_function(entry).get("description") or "")[:_DESCRIPTION_CHARS],
                }
                for entry in found
            ],
            "note": "These tools are switched on for this chat: call them by name.",
        })
    names = [entry.get("qualified_name", "") for entry in catalog]
    return json.dumps({
        "query": query,
        "switched_on": [],
        "tools": names[:_MAX_LISTED],
        "note": (
            "No tool matched. Search again in other words, or with the name of one of these tools."
            if names else "This chat has no other tools."
        ),
    })


def unknown_tool(name: str, found: list[dict[str, Any]], *, can_find: bool) -> str:
    """What the model is told when it calls a tool no one has: what its name found instead."""
    if not found:
        if can_find:
            return f"There is no tool named '{name}'. Call {FIND} with what the tool should do to find one."
        return f"There is no tool named '{name}'. Call only the tools you were given."
    return json.dumps({
        "error": f"There is no tool named '{name}'.",
        "switched_on": [
            {
                "name": entry.get("qualified_name", ""),
                "description": (_function(entry).get("description") or "")[:_DESCRIPTION_CHARS],
            }
            for entry in found
        ],
        "note": "These tools may do it and are now switched on: call one of them by its name.",
    })


# ── current_time ──────────────────────────────────────────────────


def current_time(arguments: dict[str, Any], now: datetime | None = None) -> tuple[str, bool]:
    """The date and time, in the time zone asked for; and whether it is an error."""
    now = now or datetime.now(timezone.utc)
    name = str(arguments.get("timezone") or "").strip()
    if not name or name.upper() in {"UTC", "Z", "GMT"}:
        zone: Any = timezone.utc
        name = "UTC"
    else:
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return json.dumps({
                "error": f"Unknown time zone '{name}': use an IANA name such as 'Europe/Berlin'.",
                "utc": now.isoformat(timespec="seconds"),
            }), True
    local = now.astimezone(zone)
    return json.dumps({
        "timezone": name,
        "datetime": local.isoformat(timespec="seconds"),
        "weekday": local.strftime("%A"),
    }), False
