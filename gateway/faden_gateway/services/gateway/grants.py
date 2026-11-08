"""Grants: the person lets one agent on one device run what would ask, for a while (sdk/38 §6; sdk/40 A2.1).

A ``time`` grant covers one tool; a ``work`` grant covers ``read`` and ``write`` calls in named places
(desktop 37 D4) -- a call's place is the argument its contract's ``target_argument`` points at. Only a call
that would ask because of the chat's tool mode looks a grant up (``GatewayService._covered``), and a covered
call runs instead of asking: never one the mode hides, never a destructive or unclassified tool, never the
author's ``approval: always``, and never one an admin's rule or a policy asks about (sdk/38 §4.3).

The budget and the expiry are checked and counted in one conditional ``UPDATE`` with the database's clock,
never read first and written after, so several workers and a delegated callee share one budget exactly
(ASVS 5.0 2.3.3, 2.3.4; 39 §4). A grant revoked or paused holds from the next call (ASVS 5.0 V8.3.2); a call
already running finishes (owner, 2026-11-07). A lookup that fails covers nothing: the call asks.

A ``bypass`` grant (sdk/09 §3.12, sdk/40 A3) belongs to one conversation and covers, while the chat's level
is Bypass, what its mode would ask about -- any class, still never past the floor. Every call that runs under
it is recorded with its arguments (L-60), whether it would have asked or not (:func:`record`). At the end of
each turn the device reports the calls it ran under it, and the gateway flags any difference (:func:`report`,
L-61). A delegated callee never uses it (L-58). It ends when the installation turns Bypass off
(:func:`end_bypass`) and is deleted with its calls after the retention (:func:`purge_bypass`, L-65).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Integer, String, bindparam, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from faden_gateway.db.models.gateway import (
    ApprovalGrant,
    ApprovalGrantCall,
    ApprovalGrantChange,
    ApprovalGrantFlag,
)

logger = logging.getLogger(__name__)

#: The kinds built so far; ``plan`` waits for the plan model (desktop 16 F-12).
KINDS = ("time", "work", "bypass")
#: A ``time`` or ``work`` grant never covers more than these (sdk/38 §4.3: never destructive, never
#: unclassified).
GRANT_CLASSES = ("read", "write")
#: What a ``bypass`` grant covers: every class (sdk/38 §4.1), never past the floor.
BYPASS_CLASSES = ("read", "write", "destructive", "unknown")
PLACE_TYPES = ("document", "folder", "site", "app", "desk")
#: The longest grant while the tenant has no setting of its own (sdk/38 D5: 2 hours).
DEFAULT_LONGEST_MINUTES = 120
#: The standing approval an agent's ``standing`` must offer for each kind (sdk/38 §5).
STANDING = {"time": "time", "work": "grant"}
#: What a grant records of each covered call: ``work`` and ``bypass`` their arguments too (L-60).
EVIDENCE = {"time": "decisions", "work": "calls", "bypass": "calls"}
#: The evidence levels, from the least recorded: the call; its arguments too; and its outcome and a summary of
#: its result (L-60).
EVIDENCE_LEVELS = ("decisions", "calls", "results")
#: How much of a result a ``results`` grant keeps, after the gateway's PII masking (L-60).
RESULT_CHARS = 500
#: How long after a covered call of the client's tool its result is looked for in the next request.
RESULT_WINDOW = timedelta(hours=1)


# -- what a call is checked against -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Call:
    """A call about to run, as a grant is checked against it."""

    request_id: str
    call_id: str
    tool_id: str
    tool_class: str
    #: The author's ``approval`` for the tool; ``always`` is never covered.
    approval: str | None = None
    #: ``(type, value)`` of the call's place, from the contract's ``target_argument``.
    place: tuple[str, str] | None = None
    arguments: Mapping[str, Any] | None = None
    #: Whether the chat's effective level is Bypass: only then does a ``bypass`` grant cover the call.
    bypass: bool = False
    #: ``client`` (the device runs the call) or ``gateway``.
    side: str = "gateway"


def pointer(document: Any, path: str) -> Any:
    """The value at the JSON pointer *path* (RFC 6901) in *document*; ``None`` when there is none."""
    found = document
    for token in path.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(found, Mapping) and key in found:
            found = found[key]
        elif isinstance(found, list) and key.isdigit() and int(key) < len(found):
            found = found[int(key)]
        else:
            return None
    return found


def place_of(
    contract_entry: Mapping[str, Any] | None, arguments: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Where a call acts: its tool's ``target_type`` and the text its ``target_argument`` points at."""
    if not contract_entry or arguments is None:
        return None
    kind, path = contract_entry.get("target_type"), contract_entry.get("target_argument")
    if kind not in PLACE_TYPES or not isinstance(path, str) or not path.startswith("/"):
        return None
    value = pointer(arguments, path)
    return (str(kind), value) if isinstance(value, str) and value else None


def _path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").casefold()


def _origin(value: str) -> str | None:
    parts = urlsplit(value if "://" in value else f"https://{value}")
    try:
        host, port = parts.hostname, parts.port
    except ValueError:  # a port that is not a number
        return None
    if not host:
        return None
    return f"{(parts.scheme or 'https').lower()}://{host.lower()}{f':{port}' if port else ''}"


def _within(folder: str, value: str) -> bool:
    """Whether *value* is *folder* or lies under it; a path that climbs out (``..``) lies nowhere."""
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        return False
    return (_path(value) + "/").startswith(_path(folder) + "/")


def place_in(place: tuple[str, str], target: Mapping[str, Any]) -> bool:
    """Whether *place* lies in *target*.

    Documents, apps and desks by identity, folders by path, sites by origin.
    """
    kind, value = place
    if target.get("type") != kind:
        return False
    for named in target.get("identifiers") or ():
        if not isinstance(named, str) or not named:
            continue
        if kind == "folder" and _within(named, value):
            return True
        if kind == "site" and _origin(value) is not None and _origin(value) == _origin(named):
            return True
        if kind == "document" and _path(value) == _path(named):
            return True
        if kind in ("app", "desk") and value.casefold() == named.casefold():
            return True
    return False


def in_scope(kind: str, scope: Mapping[str, Any], call: Call) -> bool:
    """Whether a grant of *kind* and *scope* covers *call* -- the floor and the classes first."""
    if kind == "bypass":
        return call.bypass and call.tool_class in BYPASS_CLASSES and call.approval != "always"
    if call.tool_class not in GRANT_CLASSES or call.approval == "always":
        return False
    if kind == "time":
        return list(scope.get("tools") or ()) == [call.tool_id]
    if kind == "work":
        if call.tool_class not in (scope.get("classes") or ()) or call.place is None:
            return False
        return any(
            place_in(call.place, target) and call.tool_class in (target.get("actions") or GRANT_CLASSES)
            for target in scope.get("targets") or ()
        )
    return False


# -- whose grants -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Owner:
    """Whose calls may use a grant.

    One person, one agent and its version, one device, maybe one conversation.
    """

    tenant_id: str
    user_id: str
    agent_id: str
    agent_version: int | None
    device_id: str
    conversation_id: str | None = None


def _device_owned(owner: Owner) -> Any:
    """The owner's grants on its device, whichever version of the agent they were made under."""
    return (
        (ApprovalGrant.tenant_id == owner.tenant_id) & (ApprovalGrant.user_id == owner.user_id)
        & (ApprovalGrant.agent_id == owner.agent_id) & (ApprovalGrant.device_id == owner.device_id)
    )


def _owned(owner: Owner) -> Any:
    """The owner's grants. A new version of the agent ends them: what it may do is the version's word."""
    return _device_owned(owner) & (ApprovalGrant.agent_version == owner.agent_version)


def _clock() -> Any:
    """The database's clock as the check runs; ``now()`` would be when its transaction began."""
    return func.clock_timestamp()


def _usable() -> Any:
    """Active, unexpired by the database's clock, and within its budget."""
    return (
        (ApprovalGrant.status == "active") & (ApprovalGrant.expires_at > _clock())
        & or_(ApprovalGrant.max_calls.is_(None), ApprovalGrant.calls_used < ApprovalGrant.max_calls)
    )


# -- covering a call --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """A grant as covering a call needs it: no more is read on that path."""

    id: uuid.UUID
    kind: str
    scope: Mapping[str, Any]
    evidence_level: str


_CANDIDATE = (ApprovalGrant.id, ApprovalGrant.kind, ApprovalGrant.scope, ApprovalGrant.evidence_level)
_RECORDED = ApprovalGrantCall.__table__.c

# The two statements of covering a call, built once: making them anew for each call cost more CPU in the
# gateway than the database spent running them (sdk/40 A2.1, the load runs).
_CANDIDATES = select(*_CANDIDATE).where(
    ApprovalGrant.tenant_id == bindparam("tenant_id"), ApprovalGrant.user_id == bindparam("user_id"),
    ApprovalGrant.agent_id == bindparam("agent_id"), ApprovalGrant.device_id == bindparam("device_id"),
    ApprovalGrant.agent_version.is_not_distinct_from(bindparam("agent_version", type_=Integer)),
    _usable(),
    or_(
        ApprovalGrant.conversation_id.is_(None),
        ApprovalGrant.conversation_id == bindparam("conversation_id", type_=String),
    ),
).order_by(ApprovalGrant.expires_at)
_COUNTED = (
    update(ApprovalGrant).where(ApprovalGrant.id == bindparam("grant"), _usable())
    .values(calls_used=ApprovalGrant.calls_used + 1)
    .returning(ApprovalGrant.id.label("grant_id"), _clock().label("at"))
    .cte("counted")
)
_COUNT_AND_RECORD = insert(ApprovalGrantCall).from_select(
    ["id", "grant_id", "request_id", "call_id", "tool_id", "target", "arguments", "created_at", "side"],
    select(
        bindparam("record", type_=_RECORDED.id.type), _COUNTED.c.grant_id,
        bindparam("request_id", type_=_RECORDED.request_id.type),
        bindparam("call_id", type_=_RECORDED.call_id.type),
        bindparam("tool_id", type_=_RECORDED.tool_id.type),
        bindparam("target", type_=_RECORDED.target.type),
        bindparam("arguments", type_=_RECORDED.arguments.type), _COUNTED.c.at,
        bindparam("side", type_=_RECORDED.side.type),
    ),
).add_cte(_COUNTED).returning(ApprovalGrantCall.id)


@asynccontextmanager
async def autocommit(bind: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection on which each statement is its own transaction.

    The count holds the grant's row lock only while the server runs it, never across a round trip back to this
    worker -- 50 callers on one grant would otherwise queue behind each other's commits.
    """
    async with bind.connect() as conn:
        yield await conn.execution_options(isolation_level="AUTOCOMMIT")


async def candidates(
    conn: AsyncConnection, owner: Owner, call: Call, kinds: Sequence[str] = KINDS,
) -> list[Candidate]:
    """The owner's usable grants of *kinds* that cover *call*, the one ending first first.

    Read only: nothing counts here.
    """
    rows = (await conn.execute(_CANDIDATES, {
        "tenant_id": owner.tenant_id, "user_id": owner.user_id, "agent_id": owner.agent_id,
        "device_id": owner.device_id, "agent_version": owner.agent_version,
        "conversation_id": owner.conversation_id,
    })).all()
    found = [Candidate(row.id, row.kind, row.scope or {}, row.evidence_level) for row in rows]
    return [g for g in found if g.kind in kinds and in_scope(g.kind, g.scope, call)]


async def load(conn: AsyncConnection, grant_id: uuid.UUID) -> Candidate | None:
    """One grant by its id, as a delegated callee's calls count against the caller's (D12).

    Never a Bypass grant: a delegated or receiving agent does not inherit Bypass (sdk/09 L-58).
    """
    row = (await conn.execute(
        select(*_CANDIDATE).where(ApprovalGrant.id == grant_id, ApprovalGrant.kind != "bypass"),
    )).first()
    return Candidate(row.id, row.kind, row.scope or {}, row.evidence_level) if row else None


async def consume(conn: AsyncConnection, grant: Candidate, call: Call) -> uuid.UUID | None:
    """Count *call* against *grant* if it is still usable, and record it; the record's id, ``None`` if not.

    One statement does both (39 §4 ``CONSUME``): the conditional ``UPDATE`` checks status, expiry and budget
    and counts, and the call's evidence row is inserted from what it returns -- with its arguments when the
    grant records calls or results (L-60), and the time the count was made. Nothing can come between the check
    and the count, nor between the count and its record; another worker waits on the row only while the server
    runs it.
    """
    arguments = None
    if grant.evidence_level in ("calls", "results") and call.arguments is not None:
        arguments = dict(call.arguments)
    recorded = await conn.execute(_COUNT_AND_RECORD, {
        "grant": grant.id, "record": uuid.uuid4(), "request_id": call.request_id[:128],
        "call_id": call.call_id[:128], "tool_id": call.tool_id[:256],
        "target": call.place[1] if call.place else None, "arguments": arguments, "side": call.side,
    })
    row = recorded.first()
    return row[0] if row is not None else None


@dataclass(frozen=True, slots=True)
class Covered:
    """A covered call, counted: its grant, its evidence row, and what the grant records."""

    grant_id: uuid.UUID
    record_id: uuid.UUID
    evidence_level: str


async def cover_call(
    bind: AsyncEngine, owner: Owner, call: Call, kinds: Sequence[str] = KINDS,
) -> Covered | None:
    """The grant that covers and counts *call*, with its record, or ``None``; on a connection of its own.

    A lookup that fails covers nothing: the call asks rather than runs (sdk/38 §6, 39 F5).
    """
    try:
        async with autocommit(bind) as conn:
            for grant in await candidates(conn, owner, call, kinds):
                record_id = await consume(conn, grant, call)
                if record_id is not None:
                    return Covered(grant.id, record_id, grant.evidence_level)
    except Exception:
        logger.exception("The grant lookup failed; the call asks")
    return None


async def cover(
    bind: AsyncEngine, owner: Owner, call: Call, kinds: Sequence[str] = KINDS,
) -> uuid.UUID | None:
    """The grant that covers and counts *call*, or ``None`` (:func:`cover_call` without the record)."""
    covered = await cover_call(bind, owner, call, kinds)
    return covered.grant_id if covered is not None else None


async def record_call(bind: AsyncEngine, owner: Owner, call: Call) -> Covered | None:
    """Record a call that runs anyway (a read, or a write at Auto) against the chat's Bypass grant (L-60).

    Every call under Bypass is evidence, not only those it spared a question; it counts against the grant's
    budget as they do. ``None``: no Bypass grant (the call runs as at Auto, unrecorded), or the record
    failed -- logged, and the call still runs, as it would without Bypass.
    """
    if not call.bypass:
        return None
    return await cover_call(bind, owner, call, kinds=("bypass",))


async def record(bind: AsyncEngine, owner: Owner, call: Call) -> uuid.UUID | None:
    """:func:`record_call`'s grant id."""
    covered = await record_call(bind, owner, call)
    return covered.grant_id if covered is not None else None


# -- the results of covered calls (L-60, the start of L-61's own sight) ------------------------


def summary(text: Any) -> str:
    """What a ``results`` grant keeps of a result: its first :data:`RESULT_CHARS` characters."""
    return str(text or "")[:RESULT_CHARS]


async def note_result(bind: AsyncEngine, record_id: uuid.UUID, *, error: bool, text: Any) -> bool:
    """Keep a covered gateway call's outcome and the start of its result; whether its record was there.

    *text* is the result as the model gets it, after the gateway's PII masking; for a failed call, its error.
    Only for a record of a ``results`` grant. A failure is logged: the call has run, and its record stays.
    """
    try:
        async with autocommit(bind) as conn:
            done = await conn.execute(
                update(ApprovalGrantCall).where(ApprovalGrantCall.id == record_id).values(
                    outcome="error" if error else "ok", result=summary(text), result_at=_clock(),
                ),
            )
            return bool(done.rowcount)
    except Exception:
        logger.exception("The result of a covered call could not be recorded")
        return False


def client_outcome(text: str) -> str:
    """How a client tool's result reads: ``error`` when it starts as the SDKs word a failure (``Error: ...``)."""
    return "error" if text.lstrip().lower().startswith("error") else "ok"


async def note_client_results(bind: AsyncEngine, owner: Owner, results: Mapping[str, str]) -> int:
    """The gateway's own sight of the client's results: *results* maps a tool call id to its message text.

    The device sends a tool's result in its next request. The owner's covered client calls of the last hour
    with those ids get ``result_at`` (the gateway saw the result come back, sdk/09 L-61), and those of a
    ``results`` grant also its outcome and the start of the text, which is after the request's egress scan
    (L-60). Each record is noted once. How many were noted; a failure is logged and notes nothing.
    """
    ids = [str(call_id)[:128] for call_id in results if call_id]
    if not ids:
        return 0
    try:
        async with autocommit(bind) as conn:
            found = (await conn.execute(
                select(ApprovalGrantCall.id, ApprovalGrantCall.call_id, ApprovalGrant.evidence_level)
                .join(ApprovalGrant, ApprovalGrant.id == ApprovalGrantCall.grant_id)
                .where(
                    # Only grants that were still running within the window: the owner's older ones are many.
                    _device_owned(owner), ApprovalGrant.expires_at > _clock() - RESULT_WINDOW,
                    ApprovalGrantCall.call_id.in_(ids), ApprovalGrantCall.side == "client",
                    ApprovalGrantCall.result_at.is_(None), ApprovalGrantCall.created_at > _clock() - RESULT_WINDOW,
                ),
            )).all()
            for row in found:
                text = results.get(row.call_id, "")
                values: dict[str, Any] = {"result_at": _clock()}
                if row.evidence_level == "results":
                    values |= {"outcome": client_outcome(text), "result": summary(text)}
                await conn.execute(
                    update(ApprovalGrantCall)
                    .where(ApprovalGrantCall.id == row.id, ApprovalGrantCall.result_at.is_(None))
                    .values(**values),
                )
            return len(found)
    except Exception:
        logger.exception("The results of covered client calls could not be noted")
        return 0


async def still_usable(bind: Any, grant_id: uuid.UUID) -> bool:
    """Whether a grant that covered a call is still active and unexpired.

    The check after a wait; nothing counts. Its budget is not checked again: the call counted when it was
    covered. A failed read: not usable.
    """
    try:
        async with AsyncSession(bind) as session:
            found = (await session.execute(
                select(ApprovalGrant.id).where(
                    ApprovalGrant.id == grant_id, ApprovalGrant.status == "active",
                    ApprovalGrant.expires_at > _clock(),
                ),
            )).first()
            return found is not None
    except Exception:
        logger.exception("The grant could not be read again; the call asks")
        return False


# -- what the person grants -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Granted:
    """What a grant request becomes within the agent's limits (the server may grant part, RFC 9396)."""

    kind: str
    scope: dict[str, Any]
    minutes: int
    max_calls: int | None
    #: What allowed it: the agent's version and the longest grant applied.
    policy_version: str
    #: What was asked for and not granted, for the answer.
    cut: list[str] = field(default_factory=list)


class RefusedError(ValueError):
    """A grant the agent's limits do not allow at all; ``code`` for the API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def longest(agent: Any, tenant_longest: int = DEFAULT_LONGEST_MINUTES) -> int:
    """The longest grant for *agent*: its own, never longer than the installation's (sdk/38 §5)."""
    return min(agent.max_grant_minutes or tenant_longest, tenant_longest)


# -- limits from extensions (``registry.grant_limits``) ---------------------------------------


class LimitsUnavailableError(RuntimeError):
    """A grant limit failed, or answered what is no limit: no grant is made, and each call asks (fail closed)."""


@dataclass(frozen=True, slots=True)
class Limits:
    """What the extensions' grant limits say together: the shortest longest grant, the most recorded level."""

    longest_minutes: int | None = None
    evidence_level: str | None = None

    def longest(self, installation: int) -> int:
        """The installation's longest grant, narrowed by the extensions'."""
        return min(installation, self.longest_minutes) if self.longest_minutes is not None else installation

    def evidence(self, kind: str) -> str:
        """What a grant of *kind* records: the extensions' level, else core's default for the kind."""
        return self.evidence_level or EVIDENCE[kind]


async def extension_limits(rules: Sequence[Any], ctx: Any) -> Limits:
    """What every rule in *rules* (``registry.grant_limits``) says of the grant in *ctx*, combined.

    Raises :class:`LimitsUnavailableError` when one fails or answers a duration that is no whole number of
    minutes above zero, or an unknown level: a limit that cannot be applied must not widen a grant.
    """
    minutes: list[int] = []
    levels: list[str] = []
    for rule in rules:
        try:
            answer = await rule(ctx)
        except Exception as exc:
            logger.warning("A grant limit failed; no grant is made: %s", exc, exc_info=True)
            raise LimitsUnavailableError(str(exc)) from exc
        if answer is None:
            continue
        found = getattr(answer, "longest_minutes", None)
        if found is not None:
            if isinstance(found, bool) or not isinstance(found, int) or found < 1:
                msg = f"A grant limit answered {found!r} as the longest grant"
                raise LimitsUnavailableError(msg)
            minutes.append(found)
        level = getattr(answer, "evidence_level", None)
        if level is not None:
            if level not in EVIDENCE_LEVELS:
                msg = f"A grant limit answered {level!r} as the evidence level"
                raise LimitsUnavailableError(msg)
            levels.append(level)
    return Limits(
        longest_minutes=min(minutes) if minutes else None,
        evidence_level=max(levels, key=EVIDENCE_LEVELS.index) if levels else None,
    )


def limit(
    *, kind: str, tools: Sequence[str], classes: Sequence[str], targets: Sequence[Mapping[str, Any]],
    minutes: int, max_calls: int | None, agent: Any, tenant_longest: int = DEFAULT_LONGEST_MINUTES,
    bypass_allowed: bool = False,
) -> Granted:
    """The grant the agent's limits allow for this request: kinds, standing, classes and the longest duration.

    *agent* is the ``AgentProfile``; *tenant_longest* the installation's longest grant; *bypass_allowed*
    whether a Bypass grant may be made now -- the installation's switch, the agent's highest level and the
    ceilings (sdk/09 L-57), which the caller works out. Raises :class:`RefusedError` when nothing of it can be
    granted.
    """
    if kind not in KINDS:
        raise RefusedError("grant_kind_unavailable", f"Grants of kind '{kind}' are not available.")
    span = longest(agent, tenant_longest)
    cut = ["minutes"] if minutes > span else []
    if kind == "bypass":
        if not bypass_allowed:
            raise RefusedError(
                "bypass_not_allowed", "Bypass is not allowed here: by the organisation, or for this agent.",
            )
        return Granted(
            "bypass", {"classes": list(BYPASS_CLASSES)}, min(minutes, span), max_calls,
            f"v{agent.version}/{span}m/bypass", cut,
        )
    if agent.standing is not None and STANDING[kind] not in agent.standing:
        raise RefusedError("grant_not_offered", "The agent does not offer this kind of grant.")
    longest_minutes = span
    policy = f"v{agent.version}/{longest_minutes}m"
    if kind == "time":
        if len(tools) != 1:
            raise RefusedError("grant_scope_invalid", "A grant for a time covers exactly one tool.")
        return Granted("time", {"tools": list(tools)}, min(minutes, longest_minutes), max_calls, policy, cut)
    allowed = [
        c for c in GRANT_CLASSES if c in classes and (agent.grant_classes is None or c in agent.grant_classes)
    ]
    if not allowed:
        raise RefusedError(
            "grant_scope_invalid", "A grant for a stretch of work covers reads, writes or both.",
        )
    if len(allowed) < len(set(classes)):
        cut.append("classes")
    places = [
        {
            "type": t["type"], "identifiers": list(t["identifiers"]),
            "actions": [a for a in (t.get("actions") or allowed) if a in allowed],
        }
        for t in targets
    ]
    places = [p for p in places if p["actions"]]
    if not places:
        raise RefusedError("grant_scope_invalid", "A grant for a stretch of work names the places it covers.")
    return Granted(
        "work", {"classes": allowed, "targets": places}, min(minutes, longest_minutes), max_calls, policy,
        cut,
    )


async def issue(
    session: AsyncSession, owner: Owner, granted: Granted, *, shown_text_hash: str | None = None,
    client_version: str | None = None, reason: str | None = None, evidence_level: str | None = None,
) -> ApprovalGrant:
    """Store a grant; it starts and ends by the database's clock. Committed.

    The stored row is the record of the switch-on that the device waits for (sdk/09 L-59): who, which device,
    agent and version, conversation, scope, expiry, what allowed it, the client and the person's reason.
    *evidence_level*: what its covered calls leave behind (:data:`EVIDENCE_LEVELS`); core's default for the
    kind without one.
    """
    grant = (await session.execute(
        insert(ApprovalGrant).values(
            id=uuid.uuid4(), kind=granted.kind, tenant_id=owner.tenant_id, user_id=owner.user_id,
            agent_id=owner.agent_id, agent_version=owner.agent_version, device_id=owner.device_id,
            conversation_id=owner.conversation_id, scope=granted.scope,
            expires_at=func.now() + timedelta(minutes=granted.minutes), max_calls=granted.max_calls,
            calls_used=0, status="active", policy_version=granted.policy_version,
            evidence_level=evidence_level or EVIDENCE[granted.kind], shown_text_hash=shown_text_hash,
            client_version=client_version, reason=reason,
        ).returning(ApprovalGrant),
    )).scalar_one()
    await session.commit()
    return grant


async def server_now(session: AsyncSession) -> datetime:
    """The database's clock, which every grant's start and end are taken by."""
    return (await session.execute(select(_clock()))).scalar_one()


async def live(session: AsyncSession, owner: Owner) -> list[ApprovalGrant]:
    """The owner's grants that are active or paused, unexpired and not spent.

    The marker's and Settings' list.
    """
    return list((await session.execute(
        select(ApprovalGrant).where(
            _owned(owner),
            ApprovalGrant.status.in_(("active", "paused")),
            ApprovalGrant.expires_at > _clock(),
            or_(ApprovalGrant.max_calls.is_(None), ApprovalGrant.calls_used < ApprovalGrant.max_calls),
        ).order_by(ApprovalGrant.expires_at),
    )).scalars().all())


async def _change(
    session: AsyncSession, grant_id: uuid.UUID, where: Any, values: dict[str, Any],
) -> ApprovalGrant | None:
    changed = (await session.execute(
        update(ApprovalGrant)
        .where(ApprovalGrant.id == grant_id, where)
        .values(**values)
        .returning(ApprovalGrant),
    )).scalar_one_or_none()
    await session.commit()
    return changed


async def revoke(
    session: AsyncSession, grant_id: uuid.UUID, *, tenant_id: str, user_id: str, agent_id: str | None = None,
    by: str = "person",
) -> ApprovalGrant | None:
    """End one of the person's grants, from the next call on; ``None`` when there is no such grant of theirs.

    Revoking an ended grant again changes nothing and still answers with it.
    """
    mine = (ApprovalGrant.tenant_id == tenant_id) & (ApprovalGrant.user_id == user_id)
    if agent_id is not None:
        mine &= ApprovalGrant.agent_id == agent_id
    changed = await _change(
        session, grant_id, mine & (ApprovalGrant.status != "revoked"),
        {"status": "revoked", "revoked_at": func.now(), "revoked_by": by},
    )
    if changed is not None:
        return changed
    return (await session.execute(
        select(ApprovalGrant).where(ApprovalGrant.id == grant_id, mine),
    )).scalar_one_or_none()


async def pause(session: AsyncSession, grant_id: uuid.UUID, owner: Owner) -> ApprovalGrant | None:
    """Pause an active, unexpired grant of the owner's device; the clock keeps running.

    ``None``: none to pause.
    """
    return await _change(
        session, grant_id,
        _device_owned(owner) & (ApprovalGrant.status == "active") & (ApprovalGrant.expires_at > _clock()),
        {"status": "paused", "paused_at": func.now()},
    )


async def resume(session: AsyncSession, grant_id: uuid.UUID, owner: Owner) -> ApprovalGrant | None:
    """Resume a paused, unexpired grant of the owner's device. ``None``: none to resume."""
    return await _change(
        session, grant_id,
        _device_owned(owner) & (ApprovalGrant.status == "paused") & (ApprovalGrant.expires_at > _clock()),
        {"status": "active", "paused_at": None},
    )


async def extend(
    session: AsyncSession, grant_id: uuid.UUID, owner: Owner, *, minutes: int,
    targets: Sequence[Mapping[str, Any]], longest_minutes: int, shown_text_hash: str | None = None,
) -> tuple[ApprovalGrant | None, list[str]]:
    """Add time and places to a running grant of the owner's device, in place; the grant and what was cut.

    The grant grows, never past ``issued_at`` plus the longest grant (owner, 2026-11-07: every grant ends,
    D5); the places hold only the classes it already covers, so an extension never widens what it lets the
    agent do. Each extension is kept with what the card showed (``approval_grant_change``). ``None``: no such
    running grant. Raises :class:`RefusedError` when nothing can be added, and ``bypass_not_extendable`` for a
    Bypass grant: it runs for the time its confirmation chose, and more time is a new Bypass with the
    confirmation again (sdk/09 L-58; ASVS 5.0 V8.3.1: the server refuses, whatever the device sends).
    Committed.
    """
    try:
        grant = (await session.execute(
            select(ApprovalGrant).where(
                ApprovalGrant.id == grant_id, _owned(owner), ApprovalGrant.status.in_(("active", "paused")),
                ApprovalGrant.expires_at > _clock(),
            ).with_for_update(),
        )).scalar_one_or_none()
        if grant is None:
            await session.rollback()
            return None, []
        if grant.kind == "bypass":
            raise RefusedError(
                "bypass_not_extendable",
                "A Bypass grant is not extended: more time is a new Bypass, confirmed again.",
            )
        added = _added_places(grant, targets)
        cap = grant.issued_at + timedelta(minutes=longest_minutes)
        wanted = grant.expires_at + timedelta(minutes=minutes)
        end = max(grant.expires_at, min(wanted, cap))
        seconds = int((end - grant.expires_at).total_seconds())
        if seconds <= 0 and not added:
            raise RefusedError(
                "grant_at_longest",
                "The grant is as long as it may be; a new grant needs the full card again.",
            )
        before = grant.expires_at
        if added:
            grant.scope = {**grant.scope, "targets": [*grant.scope.get("targets", []), *added]}
        grant.expires_at = end
        session.add(ApprovalGrantChange(
            id=uuid.uuid4(), grant_id=grant.id, added_seconds=seconds, added_targets=added or None,
            expires_before=before, expires_after=end, shown_text_hash=shown_text_hash, created_at=_clock(),
            changed_by="device",
        ))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return grant, ["minutes"] if minutes and wanted > cap else []


def _added_places(grant: ApprovalGrant, targets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The places an extension adds: only to a stretch of work, only for the classes it covers, none twice."""
    if not targets:
        return []
    if grant.kind != "work":
        raise RefusedError("grant_scope_invalid", "Only a grant for a stretch of work has places.")
    classes = list((grant.scope or {}).get("classes") or ())
    known = list((grant.scope or {}).get("targets") or ())
    added: list[dict[str, Any]] = []
    for t in targets:
        place = {
            "type": t["type"], "identifiers": list(t["identifiers"]),
            "actions": [a for a in (t.get("actions") or classes) if a in classes],
        }
        if place["actions"] and not any(_holds(k, place, classes) for k in (*known, *added)):
            added.append(place)
    if not added:
        raise RefusedError(
            "grant_scope_invalid", "The places add nothing: the grant has them, or not for what it covers.",
        )
    return added


def _holds(known: Mapping[str, Any], place: Mapping[str, Any], classes: Sequence[str]) -> bool:
    """Whether a place the grant has already allows all *place* asks for: the same place, those actions."""
    return (
        known.get("type") == place["type"] and list(known.get("identifiers") or ()) == place["identifiers"]
        and set(place["actions"]) <= set(known.get("actions") or classes)
    )


# -- Bypass: the kill switch, the device's report, the retention -------------------------------


async def end_bypass(bind: AsyncEngine, *, by: str = "kill_switch") -> int:
    """End every running Bypass grant, recorded as ended *by*; how many. Idempotent; a failure is logged.

    The installation turned Bypass off (sdk/09 L-57, L-59): no call is covered from the next one on.
    """
    try:
        async with autocommit(bind) as conn:
            ended = await conn.execute(
                update(ApprovalGrant)
                .where(
                    ApprovalGrant.kind == "bypass", ApprovalGrant.status.in_(("active", "paused")),
                    ApprovalGrant.expires_at > _clock(),
                )
                .values(status="revoked", revoked_at=_clock(), revoked_by=by),
            )
            return ended.rowcount or 0
    except Exception:
        logger.exception("The running Bypass grants could not be ended")
        return 0


@dataclass(frozen=True, slots=True)
class Report:
    """What comparing the device's report with the gateway's records found (L-61)."""

    matched: int
    #: Calls the device reported that the gateway never counted under the grant.
    device_only: list[str]
    #: Calls of the client's tools the gateway counted that the report did not name.
    gateway_only: list[str]


async def report(
    session: AsyncSession, grant_id: uuid.UUID, owner: Owner, call_ids: Sequence[str],
) -> Report | None:
    """Compare the calls the device ran under its Bypass grant with what the gateway counted; flag the rest.

    Sent at the end of each turn. The client's calls the gateway counted before this report and no report has
    named are flagged ``gateway_only``; reported calls it never counted ``device_only``. Each counted call is
    compared once. ``None``: no such Bypass grant of this device (it may have ended: a report still counts).
    Committed.
    """
    grant = (await session.execute(
        select(ApprovalGrant.id).where(
            ApprovalGrant.id == grant_id, _device_owned(owner), ApprovalGrant.kind == "bypass",
        ),
    )).scalar_one_or_none()
    if grant is None:
        return None
    named = {str(c)[:128] for c in call_ids if c}
    rows = (await session.execute(
        select(ApprovalGrantCall.id, ApprovalGrantCall.call_id, ApprovalGrantCall.tool_id).where(
            ApprovalGrantCall.grant_id == grant_id, ApprovalGrantCall.side == "client",
            ApprovalGrantCall.checked_at.is_(None),
        ),
    )).all()
    counted = {row.call_id for row in rows}
    seen_before = set((await session.execute(
        select(ApprovalGrantCall.call_id).where(
            ApprovalGrantCall.grant_id == grant_id, ApprovalGrantCall.call_id.in_(named),
            ApprovalGrantCall.checked_at.is_not(None),
        ),
    )).scalars().all()) if named else set()
    device_only = sorted(named - counted - seen_before)
    gateway_only = sorted({row.call_id for row in rows} - named)
    now = _clock()
    matched_ids = [row.id for row in rows if row.call_id in named]
    if matched_ids:
        await session.execute(
            update(ApprovalGrantCall).where(ApprovalGrantCall.id.in_(matched_ids))
            .values(reported_at=now, checked_at=now),
        )
    missed = [row for row in rows if row.call_id not in named]
    if missed:
        await session.execute(
            update(ApprovalGrantCall).where(ApprovalGrantCall.id.in_([row.id for row in missed]))
            .values(checked_at=now),
        )
    for call_id in device_only:
        session.add(ApprovalGrantFlag(
            id=uuid.uuid4(), grant_id=grant_id, call_id=call_id, tool_id=None, kind="device_only",
            created_at=now,
        ))
    for row in missed:
        session.add(ApprovalGrantFlag(
            id=uuid.uuid4(), grant_id=grant_id, call_id=row.call_id, tool_id=row.tool_id,
            kind="gateway_only", created_at=now,
        ))
    await session.commit()
    if device_only or gateway_only:
        logger.warning(
            "Bypass grant %s: the device's report differs from the gateway's "
            "(%d device only, %d gateway only)",
            grant_id, len(device_only), len(gateway_only),
        )
    return Report(matched=len(matched_ids), device_only=device_only, gateway_only=gateway_only)


async def purge_bypass(bind: AsyncEngine, days: int) -> int:
    """Delete Bypass grants that ended more than *days* ago, with their calls and flags (L-65); how many.

    A grant ended when it was revoked or, if never, when it expired. A failure is logged and deletes nothing.
    """
    try:
        async with autocommit(bind) as conn:
            ended_at = func.coalesce(ApprovalGrant.revoked_at, ApprovalGrant.expires_at)
            gone = await conn.execute(
                delete(ApprovalGrant).where(
                    ApprovalGrant.kind == "bypass", ended_at < _clock() - timedelta(days=days),
                ),
            )
            return gone.rowcount or 0
    except Exception:
        logger.exception("Bypass evidence past its retention could not be deleted")
        return 0


# -- for auditors: the records as they are, decrypted here (sdk/09 L-64) ------------------------

#: The most calls one grant's evidence lists, and the widest window and most calls a search by place reads.
EVIDENCE_CALLS = 5000
SEARCH_DAYS = 31
SEARCH_ROWS = 5000


async def evidence(session: AsyncSession, grant_id: uuid.UUID, tenant_id: str) -> dict[str, Any] | None:
    """A grant's reason and its calls' places, arguments, outcomes and summaries, decrypted; ``None``: no grant.

    For the backend's auditors only (``/internal/grant-evidence``): the place, the arguments and the result
    stay encrypted at rest under this gateway's key, which the backend does not hold.
    """
    grant = await session.get(ApprovalGrant, grant_id)
    if grant is None or grant.tenant_id != tenant_id:
        return None
    calls = (await session.execute(
        select(ApprovalGrantCall).where(ApprovalGrantCall.grant_id == grant_id)
        .order_by(ApprovalGrantCall.created_at).limit(EVIDENCE_CALLS),
    )).scalars().all()
    return {
        "grant_id": str(grant.id), "kind": grant.kind, "evidence_level": grant.evidence_level,
        "reason": grant.reason,
        "calls": [
            {
                "id": str(c.id), "call_id": c.call_id, "target": c.target, "arguments": c.arguments,
                "outcome": c.outcome, "result": c.result,
                "result_at": c.result_at.isoformat() if c.result_at else None,
            }
            for c in calls
        ],
    }


def place_matches(place: str, value: str | None) -> bool:
    """Whether the recorded place *value* is *place* or lies in it.

    The same document, app or desk; a path under a folder; a page of a site (when *place* names an origin).
    """
    if not value or not place:
        return False
    if _path(value) == _path(place) or _within(place, value):
        return True
    if "://" in place and "://" in value:
        origin = _origin(place)
        return origin is not None and _origin(value) == origin
    return False


async def find_by_place(
    session: AsyncSession, *, tenant_id: str, place: str, since: datetime, until: datetime,
    user_id: str | None = None, agent_id: str | None = None, device_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """The covered calls of the window whose place is *place* or lies in it, newest first; whether it was cut.

    The places are encrypted, so they are read and compared here: at most :data:`SEARCH_ROWS` calls of a
    window of at most :data:`SEARCH_DAYS` days (``ValueError`` beyond).
    """
    if until <= since or until - since > timedelta(days=SEARCH_DAYS):
        msg = f"A search by place covers more than nothing and at most {SEARCH_DAYS} days."
        raise ValueError(msg)
    query = (
        select(ApprovalGrantCall.id, ApprovalGrantCall.grant_id, ApprovalGrantCall.target)
        .join(ApprovalGrant, ApprovalGrant.id == ApprovalGrantCall.grant_id)
        .where(
            ApprovalGrant.tenant_id == tenant_id, ApprovalGrantCall.target.is_not(None),
            ApprovalGrantCall.created_at >= since, ApprovalGrantCall.created_at < until,
        )
    )
    for column, value in (
        (ApprovalGrant.user_id, user_id), (ApprovalGrant.agent_id, agent_id), (ApprovalGrant.device_id, device_id),
    ):
        if value:
            query = query.where(column == value)
    rows = (await session.execute(
        query.order_by(ApprovalGrantCall.created_at.desc()).limit(SEARCH_ROWS + 1),
    )).all()
    found = [
        {"id": str(row.id), "grant_id": str(row.grant_id), "target": row.target}
        for row in rows[:SEARCH_ROWS] if place_matches(place, row.target)
    ]
    return found, len(rows) > SEARCH_ROWS


# -- as the API and the events show it --------------------------------------------------------


def state(grant: ApprovalGrant, now: datetime) -> str:
    """``active``, ``paused``, ``revoked``, ``expired`` or ``spent`` (its budget used up)."""
    if grant.status == "revoked":
        return "revoked"
    if grant.expires_at <= now:
        return "expired"
    if grant.max_calls is not None and grant.calls_used >= grant.max_calls:
        return "spent"
    return grant.status


def as_dict(grant: ApprovalGrant, now: datetime) -> dict[str, Any]:
    """A grant as the API shows it, with the time left by the server's clock (the marker counts that down)."""
    return {
        "id": str(grant.id),
        "kind": grant.kind,
        "status": state(grant, now),
        "agent_id": grant.agent_id,
        "agent_version": grant.agent_version,
        "device_id": grant.device_id,
        "conversation_id": grant.conversation_id,
        "scope": grant.scope,
        "issued_at": grant.issued_at.isoformat() if grant.issued_at else None,
        "expires_at": grant.expires_at.isoformat(),
        "remaining_s": max(0, int((grant.expires_at - now).total_seconds())),
        "max_calls": grant.max_calls,
        "calls_used": grant.calls_used,
        "paused_at": grant.paused_at.isoformat() if grant.paused_at else None,
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
        "revoked_by": grant.revoked_by,
        #: What its covered calls leave on the server (``EVIDENCE_LEVELS``), for the device to say.
        "evidence_level": grant.evidence_level,
    }


def used_event(call: Mapping[str, Any], name: str, grant_id: uuid.UUID) -> dict[str, Any]:
    """A call of the client's own tool a grant covers, counted here: the client runs it without asking.

    The gateway's word, the first line; the SDK checks its own copy of the grant as the second (sdk/38 §6). A
    client that makes no grants never gets this event.
    """
    return {"type": "CUSTOM", "name": "faden.grant_used", "value": {
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "grantId": str(grant_id),
    }}


def snapshot_event(found: Sequence[ApprovalGrant], now: datetime) -> dict[str, Any]:
    """The device's live grants for the agent, at a stream's start: the SDK replaces its copy with these."""
    return {"type": "CUSTOM", "name": "faden.grants", "value": {
        "serverNow": now.isoformat(),
        "grants": [as_dict(g, now) for g in found],
    }}


async def snapshot(bind: Any, owner: Owner) -> dict[str, Any] | None:
    """:func:`snapshot_event` of the owner's live grants, on a session of its own.

    ``None``: none, or unreadable.
    """
    try:
        async with AsyncSession(bind) as session:
            found = await live(session, owner)
            return snapshot_event(found, await server_now(session)) if found else None
    except Exception:
        logger.warning("Could not read the grants of agent %s on a device", owner.agent_id, exc_info=True)
        return None
