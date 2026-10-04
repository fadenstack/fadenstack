"""What the apps that run an agent bring, compared with its contract: a review accepted in one click.

The gateway keeps each app's manifest (``agent_client_manifest`` in its database): the host tools with the
class their developer gave them, the context the app offers, its default greeting and suggestions -- and, as
``request``, tools it held back from a request because the contract did not list them. This compares the
current ones with the agent's latest version:

- ``new``: a tool the contract does not list (with an empty contract every tool is new: accepting restricts
  the agent to what is accepted);
- ``changed``: its parameters differ from the ones accepted (``schema_sha256``);
- ``riskier``: the app now calls it riskier than the contract does;
- ``unused``: in the contract, but no current app brings it;
- ``same``: nothing to do.

Nothing is applied here, and the app's word is only a suggestion: ``accept`` makes the version an admin chose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

#: Manifests seen within this long count as current.
CURRENT_DAYS = 90
#: Tools held back from requests count this long.
HELD_DAYS = 30
CLASSES = ("read", "write", "destructive")
_ORDER = {"read": 0, "write": 1, "destructive": 2}

ToolStatus = Literal["new", "changed", "riskier", "unused", "same"]


@dataclass(frozen=True)
class ManifestRow:
    """One manifest as the gateway keeps it."""

    source: str
    app_name: str
    app_version: str
    manifest: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime
    reports: int
    last_user_id: str


@dataclass
class AppSeen:
    """An app (one version) that runs the agent, or the requests whose tools were held back."""

    source: str
    name: str
    version: str
    sdk: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    reports: int
    last_user_id: str
    tools: list[str]
    context: list[dict[str, Any]]


@dataclass
class ToolReview:
    """One tool: what the apps say about it, and what the contract says."""

    name: str
    status: ToolStatus
    #: The class the app gives it (``unknown`` when it gives none); a suggestion.
    suggested_class: str
    #: The class the contract gives it; None when it gives none or does not list it.
    contract_class: str | None
    in_contract: bool
    description: str | None = None
    schema_sha256: str | None = None
    contract_schema_sha256: str | None = None
    open_world: bool | None = None
    apps: list[str] = field(default_factory=list)
    last_seen_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Review:
    """The apps that run the agent and what to take into its next version."""

    compared_with: int | None
    unrestricted: bool
    apps: list[AppSeen]
    tools: list[ToolReview]
    #: The greeting and suggestions the newest app would show, when the version's differ.
    ui: dict[str, Any] | None
    pending: int


# -- descriptions -------------------------------------------------------------------------------------------

_INSTRUCTIONS = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|previous|above|rules|prompt)\b"
    r"|\byou (must|should) (always|never|first)\b"
    r"|\balways (call|use|run) (this|me)\b"
    r"|\bbefore (answering|responding|any other tool)\b"
    r"|\bdo not (tell|mention|inform|show)\b"
    r"|\bsystem prompt\b"
    r"|<\s*/?\s*(important|system|instructions?)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HIDDEN = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_LINK = re.compile(r"https?://", re.IGNORECASE)
LONG_DESCRIPTION = 1500


def description_warnings(text: str | None) -> list[str]:
    """What looks wrong in a tool's description, which the model reads as it reads instructions.

    ``instructions`` (it tells the model what to do), ``hidden_characters`` (invisible or direction-changing
    characters), ``link``, ``long``. A hint for the reviewer, not a verdict.
    """
    if not text:
        return []
    found = []
    if _INSTRUCTIONS.search(text):
        found.append("instructions")
    if _HIDDEN.search(text):
        found.append("hidden_characters")
    if _LINK.search(text):
        found.append("link")
    if len(text) > LONG_DESCRIPTION:
        found.append("long")
    return found


# -- the review ---------------------------------------------------------------------------------------------


def _label(row: ManifestRow) -> str:
    if row.source == "request":
        return "requests"
    return f"{row.app_name} {row.app_version}".strip()


def _current(rows: list[ManifestRow], now: datetime) -> list[ManifestRow]:
    """The newest manifest of each app seen lately, and the tools held back lately; newest first."""
    latest: dict[str, ManifestRow] = {}
    held: list[ManifestRow] = []
    for row in sorted(rows, key=lambda r: r.last_seen_at, reverse=True):
        if row.source == "request":
            if row.last_seen_at >= now - timedelta(days=HELD_DAYS):
                held.append(row)
        elif row.last_seen_at >= now - timedelta(days=CURRENT_DAYS):
            latest.setdefault(row.app_name, row)
    return sorted([*latest.values(), *held], key=lambda r: r.last_seen_at, reverse=True)


def _stricter(a: str, b: str) -> str:
    if a not in _ORDER:
        return b
    if b not in _ORDER:
        return a
    return a if _ORDER[a] >= _ORDER[b] else b


def _app(row: ManifestRow) -> AppSeen:
    return AppSeen(
        source=row.source,
        name=row.app_name,
        version=row.app_version,
        sdk=_sdk(row.manifest),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        reports=row.reports,
        last_user_id=row.last_user_id,
        tools=[str(t.get("name")) for t in row.manifest.get("tools") or []],
        context=list(row.manifest.get("context") or []),
    )


def _brought(current: list[ManifestRow]) -> dict[str, ToolReview]:
    """Every tool the current apps bring, once: the newest description and schema, the strictest class."""
    brought: dict[str, ToolReview] = {}
    for row in current:  # newest first
        for tool in row.manifest.get("tools") or []:
            name = str(tool.get("name") or "")
            if not name:
                continue
            suggested = str(tool.get("class") or "unknown")
            entry = brought.get(name)
            if entry is None:
                entry = brought[name] = ToolReview(
                    name=name,
                    status="same",
                    suggested_class=suggested if suggested in _ORDER else "unknown",
                    contract_class=None,
                    in_contract=False,
                    description=tool.get("description"),
                    schema_sha256=tool.get("schema_sha256"),
                    open_world=tool.get("open_world"),
                    last_seen_at=row.last_seen_at,
                )
            else:
                entry.suggested_class = _stricter(entry.suggested_class, suggested)
            if _label(row) not in entry.apps:
                entry.apps.append(_label(row))
    return brought


def _judge(entry: ToolReview, listed: dict[str, Any] | None) -> ToolReview:
    """*entry*'s status against the contract's entry for it."""
    entry.warnings = description_warnings(entry.description)
    if listed is None:
        entry.status = "new"
        return entry
    entry.in_contract = True
    entry.contract_class = listed.get("risk") if listed.get("risk") in _ORDER else None
    entry.contract_schema_sha256 = listed.get("schema_sha256")
    accepted, now = entry.contract_schema_sha256, entry.schema_sha256
    if accepted and now and accepted != now:
        entry.status = "changed"
    elif (
        entry.suggested_class in _ORDER
        and entry.contract_class is not None
        and _ORDER[entry.suggested_class] > _ORDER[entry.contract_class]
    ):
        entry.status = "riskier"
    return entry


def _unused(contract: dict[str, dict[str, Any]], brought: dict[str, ToolReview]) -> list[ToolReview]:
    return [
        ToolReview(
            name=name, status="unused", suggested_class="unknown",
            contract_class=listed.get("risk") if listed.get("risk") in _ORDER else None, in_contract=True,
            contract_schema_sha256=listed.get("schema_sha256"),
        )
        for name, listed in contract.items()
        if name not in brought
    ]


def review(rows: list[ManifestRow], version: Any | None, *, now: datetime) -> Review:
    """The current apps' tools against *version*'s contract (its ``host_tool_contract`` and ``ui``)."""
    contract = {str(t.get("name")): t for t in (getattr(version, "host_tool_contract", None) or [])}
    current = _current(rows, now)
    brought = _brought(current)
    tools = [_judge(entry, contract.get(name)) for name, entry in brought.items()]
    if any(row.source == "app" for row in current):  # without an app's word, nothing is known to be unused
        tools += _unused(contract, brought)
    order = {"new": 0, "changed": 1, "riskier": 2, "unused": 3, "same": 4}
    tools.sort(key=lambda t: (order[t.status], t.name))
    return Review(
        compared_with=getattr(version, "version", None),
        unrestricted=not contract,
        apps=[_app(row) for row in current],
        tools=tools,
        ui=_suggested_ui(current, getattr(version, "ui", None) or {}),
        pending=sum(1 for t in tools if t.status in ("new", "changed", "riskier")),
    )


def _sdk(manifest: dict[str, Any]) -> str | None:
    sdk = manifest.get("sdk")
    if not isinstance(sdk, dict) or not sdk.get("language"):
        return None
    return f"{sdk['language']} {sdk.get('version') or ''}".strip()


def _suggested_ui(current: list[ManifestRow], ui: dict[str, Any]) -> dict[str, Any] | None:
    for row in current:
        offered = row.manifest.get("ui")
        if row.source != "app" or not isinstance(offered, dict):
            continue
        wanted = {k: offered[k] for k in ("greeting", "suggestions") if offered.get(k)}
        if wanted:
            return wanted if any(ui.get(k) != v for k, v in wanted.items()) else None
    return None


# -- accepting ----------------------------------------------------------------------------------------------


class AcceptError(ValueError):
    """What was asked cannot be taken: a tool not under review, or not in the contract."""


def accept(
    fields: dict[str, Any],
    current: Review,
    *,
    tools: list[tuple[str, str]],
    remove: list[str],
    take_ui: bool,
) -> dict[str, Any]:
    """The next version's fields, from *fields* (the latest version's).

    The accepted tools with the classes the admin gave them, without the removed ones, and with the apps'
    greeting and suggestions when *take_ui*. A class ``unknown`` leaves the tool unclassified (it always
    asks). The tool's parameters are accepted as the app sends them now (``schema_sha256``), so a later change
    shows again.
    """
    reviewed = {t.name: t for t in current.tools}
    contract = [dict(t) for t in fields.get("host_tool_contract") or []]
    by_name = {str(t.get("name")): t for t in contract}
    for name, risk in tools:
        tool = reviewed.get(name)
        if tool is None or tool.status == "unused":
            raise AcceptError(f"No app brings a tool '{name}'.")
        if name not in by_name:
            by_name[name] = {"name": name}
            contract.append(by_name[name])
        _take(by_name[name], tool, risk)
    for name in remove:
        if name not in by_name:
            raise AcceptError(f"The contract has no tool '{name}'.")
        contract.remove(by_name.pop(name))
    if fields.get("host_tool_contract") and not contract:
        # An empty contract restricts nothing: removing the last tool would allow every tool.
        raise AcceptError("The contract would be empty, which allows any tool. Keep at least one.")
    out = {**fields, "host_tool_contract": contract}
    if take_ui and current.ui:
        out["ui"] = {**(fields.get("ui") or {}), **current.ui}
    return out


def _take(entry: dict[str, Any], tool: ToolReview, risk: str) -> None:
    """The contract's *entry* for *tool*, with the admin's class and the app's parameters."""
    if risk not in (*CLASSES, "unknown"):
        raise AcceptError(f"'{risk}' is not a tool class.")
    if risk == "unknown":
        entry.pop("risk", None)
    else:
        entry["risk"] = risk
    if tool.schema_sha256:
        entry["schema_sha256"] = tool.schema_sha256
