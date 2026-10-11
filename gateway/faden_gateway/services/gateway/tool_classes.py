"""Tool classes and tool modes: what a tool does, and what the user lets tools do.

A tool is ``read``, ``write`` or ``destructive`` -- or ``unknown`` when nobody
vouched for it. The user picks a mode for the session; the mode decides, per
class, whether a tool runs, waits for the user, or is not offered at all:

    mode        read    write   destructive  unknown
    off         hidden  hidden  hidden       hidden
    read_only   run     hidden  hidden       hidden
    ask         run     ask     ask          ask        <- every session starts here
    auto        run     run     ask          ask

There is no mode that runs an unknown or destructive tool unasked: an admin
who wants that classifies the tool.

Where a class comes from, first that applies: the tenant's classification
rules (an admin's), the gateway's own tools (all read), the agent's host tool
contract (``risk``), else ``unknown``. MCP annotations are hints from the
server; they count only where a rule says to trust them (``annotations``).

``open_world`` -- a tool that reaches outside the organisation -- is carried
and shown but not acted on yet (``OPEN_WORLD_ENFORCED``).

Ceilings (EE) narrow the mode: ``registry.tool_mode_ceilings``. Core applies
whatever is registered; with nothing registered the session's mode is the
mode.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ToolClassification

logger = logging.getLogger(__name__)

#: ``open_world`` is recorded and reported, and no mode acts on it -- yet.
OPEN_WORLD_ENFORCED = False
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


class Decision(StrEnum):
    """What a mode does with a class."""

    RUN = "run"
    ASK = "ask"
    HIDDEN = "hidden"


_ORDER = {ToolMode.OFF: 0, ToolMode.READ_ONLY: 1, ToolMode.ASK: 2, ToolMode.AUTO: 3}

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


def decide(mode: ToolMode, tool: Classified) -> Decision:
    """What *mode* does with *tool*."""
    # if OPEN_WORLD_ENFORCED and tool.open_world: ...  -- a mode or an EE
    # policy on external calls goes here, when open_world is switched on.
    return _TABLE[mode][tool.tool_class]


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

    def classify(self, name: str) -> Classified:
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


async def ceiling(ceilings: Iterable[ToolModeCeiling], ctx: CeilingContext) -> ToolMode | None:
    """The narrowest of the registered ceilings; ``None`` when there is none.

    A ceiling that fails, or answers something that is not a mode, counts as
    ``read_only``: a broken restriction must not widen what tools may do.
    """
    found: list[ToolMode] = []
    for each in ceilings:
        try:
            answer = await each(ctx)
        except Exception:
            logger.exception("A tool mode ceiling failed; read_only applies")
            found.append(ToolMode.READ_ONLY)
            continue
        if answer is None:
            continue
        mode = parse_mode(str(answer))
        if mode is None:
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


async def effective(
    chosen: ToolMode | None, ceilings: Iterable[ToolModeCeiling], ctx: CeilingContext,
) -> EffectiveMode:
    """The user's (or the default) mode, narrowed by the ceilings."""
    picked = chosen or ToolMode(DEFAULT_MODE)
    top = await ceiling(ceilings, ctx)
    return EffectiveMode(mode=narrowest(picked, top), chosen=picked, ceiling=top)


def reason(mode: ToolMode, decision: Decision) -> str:
    """What the user is told about a tool the mode keeps back."""
    if decision is Decision.HIDDEN:
        return {
            ToolMode.OFF: "Tools are off in this chat.",
            ToolMode.READ_ONLY: "Only read-only tools are allowed in this chat.",
        }.get(mode, "Not allowed in this chat's tool mode.")
    return "This tool needs your approval in this chat's tool mode."
