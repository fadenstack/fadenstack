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
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ApprovalGrant, ApprovalGrantCall

logger = logging.getLogger(__name__)

#: The kinds built so far; ``plan`` waits for the plan model (desktop 16 F-12), ``bypass`` for A3.
KINDS = ("time", "work")
#: A grant never covers more than these (sdk/38 §4.3: never destructive, never unclassified).
GRANT_CLASSES = ("read", "write")
PLACE_TYPES = ("document", "folder", "site", "app", "desk")
#: The longest grant while the tenant has no setting of its own (sdk/38 D5: 2 hours).
DEFAULT_LONGEST_MINUTES = 120
#: The standing approval an agent's ``standing`` must offer for each kind (sdk/38 §5).
STANDING = {"time": "time", "work": "grant"}
#: What a grant records of each covered call: ``work`` its arguments too (L-60).
EVIDENCE = {"time": "decisions", "work": "calls"}


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


async def candidates(session: AsyncSession, owner: Owner, call: Call) -> list[ApprovalGrant]:
    """The owner's usable grants that cover *call*, the one ending first first.

    Read only: nothing counts here.
    """
    rows = (await session.execute(
        select(ApprovalGrant).where(
            _owned(owner), _usable(),
            or_(
                ApprovalGrant.conversation_id.is_(None),
                ApprovalGrant.conversation_id == owner.conversation_id,
            ),
        ).order_by(ApprovalGrant.expires_at),
    )).scalars().all()
    return [g for g in rows if in_scope(g.kind, g.scope or {}, call)]


async def consume(session: AsyncSession, grant: ApprovalGrant, call: Call) -> bool:
    """Count *call* against *grant* if it is still usable; whether it counted.

    One conditional ``UPDATE``: the check and the count cannot be split by another worker (39 §4 ``CONSUME``).
    The call's record goes in the same transaction, with its arguments when the grant records calls (L-60).
    """
    counted = (await session.execute(
        update(ApprovalGrant).where(ApprovalGrant.id == grant.id, _usable())
        .values(calls_used=ApprovalGrant.calls_used + 1).returning(ApprovalGrant.id),
    )).first()
    if counted is None:  # spent, expired, paused or revoked meanwhile: the UPDATE changed nothing
        return False
    session.add(ApprovalGrantCall(
        id=uuid.uuid4(), grant_id=grant.id, request_id=call.request_id[:128], call_id=call.call_id[:128],
        tool_id=call.tool_id[:256], target=call.place[1] if call.place else None, created_at=_clock(),
        arguments=(
            dict(call.arguments) if grant.evidence_level == "calls" and call.arguments is not None else None
        ),
    ))
    await session.commit()
    return True


async def cover(bind: Any, owner: Owner, call: Call) -> uuid.UUID | None:
    """The grant that covers and counts *call*, or ``None``; on a session of its own, committed at once.

    A lookup that fails covers nothing: the call asks rather than runs (sdk/38 §6, 39 F5).
    """
    try:
        async with AsyncSession(bind, expire_on_commit=False) as session:
            for grant in await candidates(session, owner, call):
                if await consume(session, grant, call):
                    return grant.id
    except Exception:
        logger.exception("The grant lookup failed; the call asks")
    return None


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


def limit(
    *, kind: str, tools: Sequence[str], classes: Sequence[str], targets: Sequence[Mapping[str, Any]],
    minutes: int, max_calls: int | None, agent: Any,
) -> Granted:
    """The grant the agent's limits allow for this request: kinds, standing, classes and the longest duration.

    *agent* is the ``AgentProfile``. Raises :class:`RefusedError` when nothing of it can be granted.
    """
    if kind not in KINDS:
        raise RefusedError("grant_kind_unavailable", f"Grants of kind '{kind}' are not available.")
    if agent.standing is not None and STANDING[kind] not in agent.standing:
        raise RefusedError("grant_not_offered", "The agent does not offer this kind of grant.")
    longest = agent.max_grant_minutes or DEFAULT_LONGEST_MINUTES
    cut = ["minutes"] if minutes > longest else []
    policy = f"v{agent.version}/{longest}m"
    if kind == "time":
        if len(tools) != 1:
            raise RefusedError("grant_scope_invalid", "A grant for a time covers exactly one tool.")
        return Granted("time", {"tools": list(tools)}, min(minutes, longest), max_calls, policy, cut)
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
        "work", {"classes": allowed, "targets": places}, min(minutes, longest), max_calls, policy, cut,
    )


async def issue(
    session: AsyncSession, owner: Owner, granted: Granted, *, shown_text_hash: str | None = None,
) -> ApprovalGrant:
    """Store a grant; it starts and ends by the database's clock. Committed."""
    grant = (await session.execute(
        insert(ApprovalGrant).values(
            id=uuid.uuid4(), kind=granted.kind, tenant_id=owner.tenant_id, user_id=owner.user_id,
            agent_id=owner.agent_id, agent_version=owner.agent_version, device_id=owner.device_id,
            conversation_id=owner.conversation_id, scope=granted.scope,
            expires_at=func.now() + timedelta(minutes=granted.minutes), max_calls=granted.max_calls,
            calls_used=0, status="active", policy_version=granted.policy_version,
            evidence_level=EVIDENCE[granted.kind], shown_text_hash=shown_text_hash,
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
