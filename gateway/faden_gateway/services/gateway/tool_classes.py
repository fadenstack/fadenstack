"""Tool classes and tool modes: what a tool does, and what the user lets tools do.

A tool is ``read``, ``write`` or ``destructive`` -- or ``unknown`` when nobody
vouched for it. The user picks a mode for the session; the mode decides, per
class, whether a tool runs, waits for the user, or is not offered at all:

    mode        read    write   destructive  unknown
    off         hidden  hidden  hidden       hidden
    read_only   run     hidden  hidden       hidden
    ask         run     ask     ask          ask        <- every session starts here
    auto        run     run     ask          ask
    bypass      run     run     ask*         ask*       <- * run under a Bypass grant

No mode alone runs an unknown or destructive tool unasked. Bypass (sdk/38
§4.1, sdk/09 §3.12) acts as Auto by itself; the calls Auto would ask about run
only while the conversation holds a server grant ``kind: bypass``, which the
gateway checks itself (``grants.py``; 39 F1). The level is reachable only where
the installation allows it and the agent's highest level is Bypass
(:func:`effective`); anywhere else a chosen Bypass is Auto.

The agent's author may say more per tool, in its host tool contract
(``approval``, 38 §4.3): ``always`` asks every time, and nothing the person
allowed before answers it (a floor item); ``ask`` asks even where the mode
would run the tool, though the person's "for this chat" or "always" may
answer it; ``never`` runs a ``read`` or ``write`` tool the mode would ask
about. None of them shows a tool the mode hides.

Where a class comes from, first that applies: the tenant's classification
rules (an admin's), the gateway's own tools (all read), the agent's host tool
contract (``risk``), else ``unknown``. MCP annotations are hints from the
server; they count only where a rule says to trust them (``annotations``).

``open_world`` -- a tool that reaches outside the organisation -- asks below
Auto where the table says run (``OPEN_WORLD_ENFORCED``): what it is given
leaves the organisation, and injected text can put private data into it
(sdk/51 F08 §4; OWASP Top 10 for LLM Applications 2025, LLM02, LLM06). Auto and
Bypass run it. The client is told (``openWorld`` on ``faden.approval_required``)
and offers "for this chat" at most for it.

Ceilings (EE) narrow the mode: ``registry.tool_mode_ceilings``. Core applies
whatever is registered; with nothing registered the session's mode is the
mode.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ToolClassification

logger = logging.getLogger(__name__)

#: ``open_world`` asks below Auto (:func:`decide`), and the clients are told so (the profile's ``tool_modes``).
OPEN_WORLD_ENFORCED = True
#: What a request without a session setting or header gets.
DEFAULT_MODE = "ask"
MODE_HEADER = "x-faden-tool-mode"


class ToolClass(StrEnum):
    """What a tool does."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    UNKNOWN = "unknown"


class ToolMode(StrEnum):
    """What the user lets tools do in a session, from least to most."""

    OFF = "off"
    READ_ONLY = "read_only"
    ASK = "ask"
    AUTO = "auto"
    #: Auto, plus what the conversation's Bypass grant covers (sdk/38 §4.1).
    BYPASS = "bypass"


class Decision(StrEnum):
    """What a mode does with a class."""

    RUN = "run"
    ASK = "ask"
    HIDDEN = "hidden"


_ORDER = {ToolMode.OFF: 0, ToolMode.READ_ONLY: 1, ToolMode.ASK: 2, ToolMode.AUTO: 3, ToolMode.BYPASS: 4}

_TABLE: dict[ToolMode, dict[ToolClass, Decision]] = {
    ToolMode.OFF: dict.fromkeys(ToolClass, Decision.HIDDEN),
    ToolMode.READ_ONLY: {
        ToolClass.READ: Decision.RUN, ToolClass.WRITE: Decision.HIDDEN,
        ToolClass.DESTRUCTIVE: Decision.HIDDEN, ToolClass.UNKNOWN: Decision.HIDDEN,
    },
    ToolMode.ASK: {
        ToolClass.READ: Decision.RUN, ToolClass.WRITE: Decision.ASK,
        ToolClass.DESTRUCTIVE: Decision.ASK, ToolClass.UNKNOWN: Decision.ASK,
    },
    ToolMode.AUTO: {
        ToolClass.READ: Decision.RUN, ToolClass.WRITE: Decision.RUN,
        ToolClass.DESTRUCTIVE: Decision.ASK, ToolClass.UNKNOWN: Decision.ASK,
    },
}
# What Bypass runs beyond Auto is the grant's to cover, never the table's.
_TABLE[ToolMode.BYPASS] = dict(_TABLE[ToolMode.AUTO])


def decide(mode: ToolMode, tool: Classified) -> Decision:
    """What *mode* does with *tool*, with its author's ``approval`` on top (38 §4.3, D7).

    An open-world tool asks below Auto where the table says run (:func:`asks_as_open_world`); the author's
    ``never`` still runs a read or write tool, as for any ask of the mode.
    """
    decision = _TABLE[mode][tool.tool_class]
    if decision is Decision.HIDDEN:
        return decision
    if tool.approval in ("always", "ask"):
        return Decision.ASK
    if tool.approval == "never" and tool.tool_class in (ToolClass.READ, ToolClass.WRITE):
        return Decision.RUN  # pre-approved by the author; never for a destructive or unclassified tool
    if decision is Decision.RUN and asks_as_open_world(mode, tool):
        return Decision.ASK
    return decision


def asks_as_open_world(mode: ToolMode, tool: Classified) -> bool:
    """Whether *tool* is open-world and *mode* below Auto: then it asks, and the client takes "for this chat" at most."""
    return OPEN_WORLD_ENFORCED and tool.open_world is True and _ORDER[mode] < _ORDER[ToolMode.AUTO]


def narrowest(*modes: ToolMode | None) -> ToolMode:
    """The most restrictive of *modes* (``None``: no opinion)."""
    given = [m for m in modes if m is not None]
    return min(given, key=_ORDER.__getitem__) if given else ToolMode(DEFAULT_MODE)


def parse_mode(value: str | None) -> ToolMode | None:
    """A mode from a header or a setting; ``None`` when empty or unknown."""
    try:
        return ToolMode((value or "").strip().lower()) if value else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Classified:
    """A tool's class, where it came from, and whether it reaches outside."""

    name: str
    tool_class: ToolClass
    source: str  # "rule", "gateway", "agent", "annotations", "none"
    open_world: bool | None = None
    #: The agent's author on asking: ``never``, ``ask`` or ``always`` (host tool contract); ``None``: unset.
    approval: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_class": self.tool_class.value, "class_source": self.source, "open_world": self.open_world,
        }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassRule:
    """An admin's classification rule."""

    pattern: str
    tool_class: str  # read | write | destructive | annotations
    open_world: bool | None = None


def from_annotations(annotations: Mapping[str, Any] | None) -> tuple[ToolClass, bool | None]:
    """The class MCP annotations claim (``unknown`` without any)."""
    if not annotations:
        return ToolClass.UNKNOWN, None
    open_world = annotations.get("openWorldHint")
    if annotations.get("readOnlyHint") is True:
        return ToolClass.READ, open_world
    if annotations.get("destructiveHint") is False:
        return ToolClass.WRITE, open_world
    return ToolClass.DESTRUCTIVE, open_world  # the spec's default for a tool that writes


#: The SDKs' tool search (the .NET SDK's local MCP module, the TypeScript SDK's tools on request): it only switches
#: the client's own tools on for the conversation and runs nothing itself; each tool it finds is classified and asked
#: about on its own call. So it is read unless an admin's rule or the agent's contract says otherwise. The SDKs keep
#: the name for it: an app cannot give it to a tool of its own. (sdk/51, the owner, 13 Nov 2026; Claude's and
#: Copilot's tool search does not ask either.)
SDK_TOOL_SEARCH = "find_tool"
#: The person's own skills on the device: load one, read one of its files (sdk/51 F04).
SDK_LOAD_SKILL = "load_skill"
SDK_READ_SKILL_RESOURCE = "read_skill_resource"
#: Kept by the SDKs and never offered: a skill's scripts stay off. Held back here too, should a client send it.
SDK_RUN_SKILL_SCRIPT = "run_skill_script"
#: A web search from the device (sdk/51 F06): read, and open-world, since the query leaves the organisation.
SDK_WEB_SEARCH = "web_search"

#: The SDKs' own tools, with the class and open-world mark the gateway gives each (sdk/52 §10.5, the owner, 14 Nov
#: 2026). The SDKs keep these names (0.3.0): an app cannot give one to a tool of its own, so the word reaches no other
#: tool. An admin's rule or the agent's contract still decides the class; the open-world mark stays whatever they say.
#: ``run_skill_script`` has no class here (unknown): it is always held back (:func:`sdk_tools_allowed`).
SDK_TOOLS: Mapping[str, tuple[ToolClass, bool]] = MappingProxyType({
    SDK_TOOL_SEARCH: (ToolClass.READ, False),
    SDK_LOAD_SKILL: (ToolClass.READ, False),
    SDK_READ_SKILL_RESOURCE: (ToolClass.READ, False),
    SDK_WEB_SEARCH: (ToolClass.READ, True),
})
#: Every name the SDKs keep for their own tools.
SDK_TOOL_NAMES = frozenset({*SDK_TOOLS, SDK_RUN_SKILL_SCRIPT})


def sdk_tools_allowed(*, local_mcp: bool, local_skills: bool) -> frozenset[str]:
    """The SDKs' tools the agent's profile lets reach the model; the others are held back, whatever the contract says.

    ``find_tool`` always; the skill tools where the person's own skills may run (*local_skills*: the profile's
    ``local_skills``, or developer mode); ``web_search`` where the device may attach local MCP servers (*local_mcp*),
    until core has a switch of its own for it; ``run_skill_script`` never (fail closed). The server holds them back,
    not only the SDK, so a forged request gains nothing (ASVS 5.0 V8.3.1).
    """
    allowed = {SDK_TOOL_SEARCH}
    if local_skills:
        allowed |= {SDK_LOAD_SKILL, SDK_READ_SKILL_RESOURCE}
    if local_mcp:
        allowed.add(SDK_WEB_SEARCH)
    return frozenset(allowed)


@dataclass(frozen=True)
class Classifier:
    """Classifies the tools of one request."""

    rules: tuple[ClassRule, ...] = ()
    #: The gateway's own tools: all read.
    gateway_tools: frozenset[str] = frozenset()
    #: The agent's host tool contract: name -> risk.
    contract: Mapping[str, str] | None = None
    #: MCP tools' annotations, by qualified name.
    annotations: Mapping[str, Mapping[str, Any]] | None = None
    #: The agent's host tool contract: name -> approval, for the tools that set one.
    approvals: Mapping[str, str] | None = None

    def classify(self, name: str) -> Classified:
        found = self._classify(name)
        if SDK_TOOLS.get(name, (None, False))[1] and found.open_world is not True:
            found = replace(found, open_world=True)  # the SDKs' web search reaches outside, whoever set the class
        approval = (self.approvals or {}).get(name)
        return replace(found, approval=approval) if approval else found

    def _classify(self, name: str) -> Classified:
        rule = next((r for r in self.rules if fnmatch.fnmatchcase(name, r.pattern)), None)
        if rule is not None:
            if rule.tool_class == "annotations":
                claimed, open_world = from_annotations((self.annotations or {}).get(name))
                return Classified(name, claimed, "annotations",
                                  rule.open_world if rule.open_world is not None else open_world)
            return Classified(name, ToolClass(rule.tool_class), "rule", rule.open_world)
        if name in self.gateway_tools:
            return Classified(name, ToolClass.READ, "gateway", False)
        risk = (self.contract or {}).get(name)
        if risk in ("read", "write", "destructive"):
            return Classified(name, ToolClass(risk), "agent")
        if name in SDK_TOOLS:
            tool_class, open_world = SDK_TOOLS[name]
            return Classified(name, tool_class, "sdk", open_world)
        open_world = ((self.annotations or {}).get(name) or {}).get("openWorldHint")
        return Classified(name, ToolClass.UNKNOWN, "none", open_world)


class ClassRules:
    """Each tenant's classification rules, read once per 30 s per worker."""

    def __init__(self, ttl_sec: float = 30.0) -> None:
        self.ttl_sec = ttl_sec
        self._cache: dict[str, tuple[float, tuple[ClassRule, ...]]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)

    async def for_tenant(self, session: AsyncSession, tenant_id: str) -> tuple[ClassRule, ...]:
        cached = self._cache.get(tenant_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        rows = (await session.execute(
            select(ToolClassification)
            .where(ToolClassification.tenant_id == tenant_id)
            .order_by(ToolClassification.priority, ToolClassification.created_at),
        )).scalars().all()
        rules = tuple(ClassRule(r.tool_pattern, r.tool_class, r.open_world) for r in rows)
        self._cache[tenant_id] = (time.monotonic() + self.ttl_sec, rules)
        return rules


class_rules = ClassRules()


# ---------------------------------------------------------------------------
# Ceilings, from Enterprise
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CeilingContext:
    """What a ceiling gets. Fields may be added; none is removed or renamed."""

    tenant_id: str
    user_id: str
    agent_id: str | None = None
    session_id: str | None = None
    groups: tuple[str, ...] = ()


#: ``context -> the most the user may pick``, or ``None`` for no ceiling.
ToolModeCeiling = Callable[[CeilingContext], Awaitable["ToolMode | str | None"]]


class CeilingUnavailableError(RuntimeError):
    """A ceiling failed or answered no mode, where the caller asked to be told rather than get ``read_only``."""


async def ceiling(
    ceilings: Iterable[ToolModeCeiling], ctx: CeilingContext, *, strict: bool = False,
) -> ToolMode | None:
    """The narrowest of the registered ceilings; ``None`` when there is none.

    A ceiling that fails, or answers something that is not a mode, counts as
    ``read_only``: a broken restriction must not widen what tools may do. With
    *strict* it raises :class:`CeilingUnavailableError` instead, for a caller
    that must tell "not allowed" from "cannot be checked" (a Bypass resuming).
    """
    found: list[ToolMode] = []
    for each in ceilings:
        try:
            answer = await each(ctx)
        except Exception as exc:
            if strict:
                raise CeilingUnavailableError(str(exc)) from exc
            logger.exception("A tool mode ceiling failed; read_only applies")
            found.append(ToolMode.READ_ONLY)
            continue
        if answer is None:
            continue
        mode = parse_mode(str(answer))
        if mode is None:
            if strict:
                msg = f"A tool mode ceiling answered {answer!r}"
                raise CeilingUnavailableError(msg)
            logger.error("A tool mode ceiling answered %r; read_only applies", answer)
            mode = ToolMode.READ_ONLY
        found.append(mode)
    return narrowest(*found) if found else None


@dataclass(frozen=True, slots=True)
class EffectiveMode:
    """The mode a request runs under, and why."""

    mode: ToolMode
    chosen: ToolMode
    ceiling: ToolMode | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_mode": self.chosen.value,
            "effective_tool_mode": self.mode.value,
            "tool_mode_ceiling": self.ceiling.value if self.ceiling else None,
        }


def limited(top: ToolMode | None, limit: ToolMode | None) -> ToolMode | None:
    """The ceiling *top* narrowed by an agent's highest level *limit*; Auto or above narrows nothing.

    Bypass above Auto is narrowed in :func:`effective`, so the ceiling that clients without Bypass see stays
    as it was.
    """
    if limit is None or limit in (ToolMode.AUTO, ToolMode.BYPASS):
        return top
    return narrowest(top, limit) if top is not None else limit


async def effective(
    chosen: ToolMode | None, ceilings: Iterable[ToolModeCeiling], ctx: CeilingContext,
    *, default: ToolMode | None = None, limit: ToolMode | None = None, bypass_allowed: bool = False,
) -> EffectiveMode:
    """The user's mode -- else *default*, the agent's level, else Ask -- narrowed by the ceilings and *limit*.

    *limit* is the agent's highest level (sdk/38 §5): the narrowest of it and the EE ceilings applies. Bypass
    stays Bypass only where the installation allows it (*bypass_allowed*, sdk/09 L-57) and the agent's highest
    level is Bypass; anywhere else it is Auto.
    """
    picked = chosen or default or ToolMode(DEFAULT_MODE)
    top = limited(await ceiling(ceilings, ctx), limit)
    mode = narrowest(picked, top)
    if mode is ToolMode.BYPASS and not (bypass_allowed and limit is ToolMode.BYPASS):
        mode = ToolMode.AUTO
    return EffectiveMode(mode=mode, chosen=picked, ceiling=top)


def reason(mode: ToolMode, decision: Decision, approval: str | None = None, *, open_world: bool = False) -> str:
    """What the user is told about a tool the mode keeps back, that its author has asked about, or that is open-world."""
    if decision is Decision.HIDDEN:
        return {
            ToolMode.OFF: "Tools are off in this chat.",
            ToolMode.READ_ONLY: "Only read-only tools are allowed in this chat.",
        }.get(mode, "Not allowed in this chat's tool mode.")
    if approval == "always":
        return "This agent asks for your approval every time it uses this tool."
    if approval == "ask":
        return "This agent asks for your approval before it uses this tool."
    if open_world:
        return "What this tool is given leaves your organisation: it needs your approval in this chat's tool mode."
    return "This tool needs your approval in this chat's tool mode."
