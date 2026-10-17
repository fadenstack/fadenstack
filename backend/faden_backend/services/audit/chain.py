"""Writing the audit trail: each event in its own transaction, chained by hash, copied out of the database.

- **Its own transaction.** An event is committed at once, apart from the request's work. A refusal used to
  be rolled back with the request it refused (the request's session rolls back on any error), so denials
  never reached the trail.
- **A chain.** Each event carries the hash of the one before it and its own: SHA-256 over the previous hash
  and the event's canonical JSON. Writers take a transaction-level advisory lock, so the chain stays one
  line with several workers. Changing, inserting or deleting a row breaks the chain where it happened.
- **Rows from before the chain** are covered by the first chained event, ``audit.chain.start``: it records
  how many there were and a digest over them (:func:`start_chain`).
- **Copies.** Each event is also appended, with its hash, to ``audit.jsonl`` in the audit folder (a volume
  of its own, in every backup) and written to the log as one ``AUDIT {...}`` line (so it reaches Loki).
  Someone who edits the table from the database cannot edit those from there.

Without a session factory (the test suite does not start the app) the event is written on the caller's
connection, chained the same way, without flushing the caller's own work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.services.audit import context as request_context

logger = logging.getLogger("faden.audit")

#: The first event's "previous hash".
GENESIS = "0" * 64
#: Advisory lock key for the chain (any constant; "faden-audit" in ASCII, cut to 63 bits).
_LOCK_KEY = 0x6661_6465_6E61_7564 & 0x7FFF_FFFF_FFFF_FFFF
#: The file copy starts a new file past this size; old ones are never deleted.
_ROTATE_BYTES = 64 * 1024 * 1024

@dataclass(slots=True)
class _Settings:
    """Where events are written and copied; set once at start (:func:`configure`)."""

    session_factory: async_sessionmaker[AsyncSession] | None = None
    audit_dir: Path | None = None
    dir_warned: bool = False


_settings = _Settings()


def configure(session_factory: async_sessionmaker[AsyncSession] | None, audit_dir: str | Path | None) -> None:
    """Called once at start: where events are written (their own transactions) and copied."""
    _settings.session_factory = session_factory
    _settings.audit_dir = Path(audit_dir) if audit_dir else None


# ── The canonical form ────────────────────────────────────────────────────────────


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def canonical(event: AuditEvent, *, with_chain: bool = True) -> dict[str, Any]:
    """The fields an event's hash covers, as plain values."""
    result = event.result.value if isinstance(event.result, AuditResult) else event.result
    body: dict[str, Any] = {
        "id": str(event.id),
        "time": _iso(event.time),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_email": event.actor_email,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "result": result,
        "severity": event.severity,
        "metadata_json": event.metadata_json,
        "client_ip": event.client_ip,
        "request_id": event.request_id,
        "user_agent": event.user_agent,
        "source": event.source,
    }
    if with_chain:
        body["prev_hash"] = event.prev_hash
    return body


def _dumps(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(event: AuditEvent) -> str:
    """SHA-256 over the event's canonical JSON, previous hash included."""
    return hashlib.sha256(_dumps(canonical(event)).encode("utf-8")).hexdigest()


def legacy_digest(events: Iterable[AuditEvent]) -> tuple[int, str]:
    """How many unchained events there are and a digest over them, in time order (``audit.chain.start``)."""
    digest = hashlib.sha256()
    count = 0
    for event in events:
        digest.update(_dumps(canonical(event, with_chain=False)).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


# ── Writing ───────────────────────────────────────────────────────────────────────


async def _lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})


async def _last_hash(session: AsyncSession) -> str | None:
    return (await session.execute(
        select(AuditEvent.hash).where(AuditEvent.hash.is_not(None)).order_by(AuditEvent.seq.desc()).limit(1),
    )).scalar_one_or_none()


async def _actor(session: AsyncSession, actor_id: uuid.UUID | None) -> tuple[bool, str | None]:
    """Whether the account exists, and its e-mail."""
    if actor_id is None:
        return False, None
    from faden_backend.db.models.users import User  # noqa: PLC0415 - users imports this module

    email = (await session.execute(select(User.email).where(User.id == actor_id))).scalar_one_or_none()
    return email is not None, email


@dataclass(slots=True)
class NewEvent:
    """What a caller says about an event; the writer adds who, from where, when, and the chain."""

    action: str
    target_type: str
    target_id: str
    result: AuditResult
    actor_id: uuid.UUID | None = None
    severity: str = "normal"
    metadata_json: str | None = None
    #: Overrides the request's source (the CLI's tools say ``cli``).
    source: str | None = None
    actor_email: str | None = None


async def _fill_and_chain(session: AsyncSession, new: NewEvent) -> AuditEvent:
    ctx = request_context.current()
    actor_id = new.actor_id if new.actor_id is not None else ctx.actor_id
    exists, found_email = await _actor(session, actor_id)
    email = new.actor_email or (ctx.actor_email if actor_id == ctx.actor_id else None) or found_email
    metadata_json = new.metadata_json
    if actor_id is not None and not exists:
        # An account deleted while it acted (or never stored): the event is kept all the same, its id in the
        # details, since the column must name an existing account.
        details = json.loads(metadata_json) if metadata_json else {}
        details = details if isinstance(details, dict) else {"details": details}
        metadata_json = json.dumps({**details, "actor_id": str(actor_id)})
        actor_id = None
    event = AuditEvent(
        id=uuid.uuid4(),
        time=datetime.now(UTC),
        actor_id=actor_id,
        actor_email=email,
        action=new.action[:128],
        target_type=new.target_type[:64],
        target_id=(new.target_id or "")[:256],
        result=new.result,
        severity=new.severity,
        metadata_json=metadata_json,
        client_ip=ctx.client_ip,
        request_id=ctx.request_id,
        user_agent=ctx.user_agent,
        source=(new.source or ctx.source or "system")[:16],
    )
    await _lock(session)
    event.prev_hash = await _last_hash(session) or GENESIS
    event.hash = event_hash(event)
    session.add(event)
    await session.flush()
    return event


async def append(new: NewEvent, request_session: AsyncSession | None = None) -> AuditEvent:
    """Write *new*: committed in its own transaction when the app runs, else into *request_session*."""
    request_context.mark_audited()
    if _settings.session_factory is not None:
        async with _settings.session_factory() as session:
            event = await _fill_and_chain(session, new)
            await session.commit()
    elif request_session is not None:
        # A session of its own on the caller's connection: the event joins the caller's transaction (rolled
        # back with a test), and the caller's pending work is not flushed by it.
        async with AsyncSession(bind=request_session.bind, expire_on_commit=False) as side:
            event = await _fill_and_chain(side, new)
    else:
        raise RuntimeError("The audit trail is not configured and no session was given.")
    _copy(event)
    return event


async def start_chain_in(session: AsyncSession) -> AuditEvent | None:
    """Begin the chain in *session* (not committed), unless it has begun: see :func:`start_chain`."""
    await _lock(session)
    if await _last_hash(session) is not None:
        return None
    legacy = (await session.execute(
        select(AuditEvent).where(AuditEvent.hash.is_(None)).order_by(AuditEvent.time, AuditEvent.id),
    )).scalars().all()
    count, digest = legacy_digest(legacy)
    event = AuditEvent(
        id=uuid.uuid4(),
        time=datetime.now(UTC),
        action="audit.chain.start",
        target_type="audit",
        target_id="chain",
        result=AuditResult.ALLOW,
        severity="high",
        metadata_json=json.dumps({"legacy_rows": count, "legacy_digest": digest}),
        source="system",
        prev_hash=GENESIS,
    )
    event.hash = event_hash(event)
    session.add(event)
    await session.flush()
    return event


async def start_chain(session_factory: async_sessionmaker[AsyncSession]) -> AuditEvent | None:
    """Begin the chain once: ``audit.chain.start`` covers every event written before it (an upgrade's)."""
    async with session_factory() as session:
        event = await start_chain_in(session)
        await session.commit()
    if event is not None:
        _copy(event)
    return event


def configured() -> bool:
    """Whether events get a transaction of their own (the app runs)."""
    return _settings.session_factory is not None


# ── Copies ────────────────────────────────────────────────────────────────────────


def copy_line(event: AuditEvent) -> str:
    """The event as one JSON line: its canonical form and hash."""
    return _dumps({**canonical(event), "hash": event.hash})


def _copy(event: AuditEvent) -> None:
    """Append to the file copy and write the log line; a failure is logged, never raised."""
    line = copy_line(event)
    logger.info("AUDIT %s", line)
    folder = _settings.audit_dir
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "audit.jsonl"
        if path.exists() and path.stat().st_size > _ROTATE_BYTES:
            path.rename(folder / f"audit-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        if not _settings.dir_warned:
            logger.exception("The audit trail's file copy could not be written to %s", folder)
            _settings.dir_warned = True


def read_copies(audit_dir: str | Path) -> dict[str, str]:
    """The file copies' events: id -> hash, over every ``audit*.jsonl`` in the folder."""
    found: dict[str, str] = {}
    for path in sorted(Path(audit_dir).glob("audit*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("id") and record.get("hash"):
                found[str(record["id"])] = str(record["hash"])
    return found


# ── Checking ──────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Verdict:
    """What :func:`verify` found."""

    events: int = 0
    legacy_rows: int = 0
    intact_until: datetime | None = None
    problems: list[str] | None = None

    @property
    def ok(self) -> bool:
        """Nothing was found."""
        return not self.problems


async def _legacy_problem(session: AsyncSession, start: AuditEvent) -> tuple[int, str | None]:
    """The events from before the chain against ``audit.chain.start``'s count and digest."""
    meta = json.loads(start.metadata_json or "{}")
    legacy = (await session.execute(
        select(AuditEvent).where(AuditEvent.hash.is_(None)).order_by(AuditEvent.time, AuditEvent.id),
    )).scalars().all()
    count, digest = legacy_digest(legacy)
    if (count, digest) == (meta.get("legacy_rows"), meta.get("legacy_digest")):
        return count, None
    return count, (
        f"The {meta.get('legacy_rows')} events from before the chain were changed "
        f"(now {count}, digest differs: {digest != meta.get('legacy_digest')})"
    )


def _copy_problems(events: Iterable[AuditEvent], audit_dir: str | Path) -> list[str]:
    """Events in the file copy that the database lost or changed."""
    copies = read_copies(audit_dir)
    in_db = {str(event.id): event.hash for event in events}
    problems = []
    for event_id, expected in copies.items():
        if event_id not in in_db:
            problems.append(f"Event {event_id} is in the file copy but no longer in the database")
        elif in_db[event_id] != expected:
            problems.append(f"Event {event_id} differs between the database and the file copy")
    return problems


async def verify(session: AsyncSession, audit_dir: str | Path | None = None) -> Verdict:
    """Walk the chain: every hash, every link, the rows from before it, and the file copies if any."""
    problems: list[str] = []
    verdict = Verdict(problems=problems)
    events = (await session.execute(
        select(AuditEvent).where(AuditEvent.hash.is_not(None)).order_by(AuditEvent.seq),
    )).scalars().all()
    previous = GENESIS
    for event in events:
        where = f"{_iso(event.time)} {event.action} ({event.id})"
        if event.prev_hash != previous:
            problems.append(f"{where}: its link to the event before is broken")
        if event_hash(event) != event.hash:
            problems.append(f"{where}: changed after it was written")
        if event.action == "audit.chain.start":
            verdict.legacy_rows, problem = await _legacy_problem(session, event)
            if problem:
                problems.append(problem)
        previous = event.hash or previous
        if not problems:
            verdict.intact_until = event.time
        verdict.events += 1
    if audit_dir is not None and Path(audit_dir).exists():
        problems.extend(_copy_problems(events, audit_dir))
    return verdict
