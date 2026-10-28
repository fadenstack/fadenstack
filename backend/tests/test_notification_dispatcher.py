"""The outbox dispatcher: one worker sends a message, a final failure is final.

Every backend worker runs a dispatcher. A message is claimed before it is sent, so two workers polling at the
same moment send it once; a worker that stopped while sending gives the message back when its lease is over,
and one past its lease writes nothing. A sender may say a failure is permanent: the message fails at once.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.services.extensions import registry
from faden_backend.services.notifications.dispatcher import LEASE, NotificationDispatcher
from tests.conftest import RecordingMailSender

pytestmark = pytest.mark.anyio


class _Session:
    """The test's own session, for the dispatcher's short ones."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __call__(self) -> _Session:
        return self

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


class _SlowSender(RecordingMailSender):
    """Takes a while per mail, as a mail server does: another worker polls meanwhile."""

    async def send(self, kind: str, payload: dict[str, Any]) -> None:
        """Keeps the message, after a pause."""
        await asyncio.sleep(0.05)
        await super().send(kind, payload)


class _RefusedError(Exception):
    """A sender's failure, final or not."""

    def __init__(self, text: str, *, permanent: bool) -> None:
        super().__init__(text)
        self.permanent = permanent


class _FailingSender(RecordingMailSender):
    """Fails every mail with *error*."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def send(self, kind: str, payload: dict[str, Any]) -> None:
        """Fails."""
        raise self.error


def _mail(to: str, **values: Any) -> NotificationOutbox:
    return NotificationOutbox(
        kind="password_reset",
        payload_json={"to_email": to},
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        **values,
    )


@pytest.fixture
async def committed(_engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """Sessions of their own, as each worker has: what they write is committed, and deleted afterwards."""
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(
            delete(NotificationOutbox).where(
                NotificationOutbox.payload_json["to_email"].astext.like("%@dispatch.example.com"),
            ),
        )
        await session.commit()


async def test_two_workers_polling_at_once_send_each_mail_once(
    committed: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _SlowSender()
    monkeypatch.setattr(registry, "mail_sender", sender)
    addresses = [f"m{i}@dispatch.example.com" for i in range(4)]
    async with committed() as session:
        session.add_all(_mail(to) for to in addresses)
        await session.commit()

    workers = [NotificationDispatcher(committed), NotificationDispatcher(committed)]
    await asyncio.gather(*(w._poll_once() for w in workers))  # noqa: SLF001

    sent = sorted(payload["to_email"] for _, payload in sender.sent if payload["to_email"] in addresses)
    assert sent == addresses, "each mail once, none twice"
    async with committed() as session:
        rows = (await session.execute(
            select(NotificationOutbox).where(NotificationOutbox.payload_json["to_email"].astext.in_(addresses)),
        )).scalars().all()
    assert {(r.status, r.attempt_count) for r in rows} == {("sent", 1)}


async def test_a_mail_another_worker_holds_is_left_to_it_until_its_lease_is_over(
    dbsession: AsyncSession, mail_on: RecordingMailSender, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    held = _mail("held@dispatch.example.com", status="sending", attempt_count=1, next_attempt_at=now + LEASE)
    lost = _mail("lost@dispatch.example.com", status="sending", attempt_count=1,
                 next_attempt_at=now - timedelta(seconds=1))
    dbsession.add_all([held, lost])
    await dbsession.flush()
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)

    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001

    sent = [p["to_email"] for _, p in mail_on.sent if p["to_email"].endswith("@dispatch.example.com")]
    assert sent == ["lost@dispatch.example.com"], "the stopped worker's mail goes out; the held one waits"
    await dbsession.refresh(held)
    await dbsession.refresh(lost)
    assert (held.status, held.attempt_count) == ("sending", 1)
    assert (lost.status, lost.attempt_count) == ("sent", 2)


async def test_a_worker_past_its_lease_writes_nothing(
    dbsession: AsyncSession, mail_on: RecordingMailSender, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _mail("late@dispatch.example.com")
    dbsession.add(row)
    await dbsession.flush()
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    late = NotificationDispatcher(_Session(dbsession))  # type: ignore[arg-type]

    claim = await late._claim()  # noqa: SLF001
    assert claim is not None and claim.id == row.id
    # Its lease ran out, and another worker claimed the mail again.
    taken_over = datetime.now(UTC) + LEASE + timedelta(minutes=1)
    await dbsession.execute(
        update(NotificationOutbox).where(NotificationOutbox.id == row.id).values(next_attempt_at=taken_over),
    )
    await late._deliver(claim)  # noqa: SLF001

    await dbsession.refresh(row)
    assert (row.status, row.next_attempt_at, row.sent_at) == ("sending", taken_over, None)


async def test_a_permanent_failure_is_final_at_once(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = _FailingSender(_RefusedError("login_refused: 535 5.7.8 Authentication credentials invalid", permanent=True))
    monkeypatch.setattr(registry, "mail_sender", final)
    row = _mail("final@dispatch.example.com")
    dbsession.add(row)
    await dbsession.flush()
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)

    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001

    await dbsession.refresh(row)
    assert (row.status, row.attempt_count) == ("failed", 1)
    assert row.last_error == "login_refused: 535 5.7.8 Authentication credentials invalid"


@pytest.mark.parametrize("error", [
    _RefusedError("connect_failed: mail.example.com:587: refused", permanent=False),
    RuntimeError("no answer"),
])
async def test_any_other_failure_is_tried_again_later(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    monkeypatch.setattr(registry, "mail_sender", _FailingSender(error))
    row = _mail(f"again-{uuid.uuid4().hex[:6]}@dispatch.example.com")
    dbsession.add(row)
    await dbsession.flush()
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    before = datetime.now(UTC)

    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001

    await dbsession.refresh(row)
    assert (row.status, row.attempt_count, row.last_error) == ("pending", 1, str(error))
    assert row.next_attempt_at >= before + timedelta(seconds=60), "after a pause, not in this poll"


async def test_after_the_last_attempt_it_gives_up(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "mail_sender", _FailingSender(RuntimeError("no answer")))
    row = _mail("last@dispatch.example.com", attempt_count=4)
    dbsession.add(row)
    await dbsession.flush()
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)

    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001

    await dbsession.refresh(row)
    assert (row.status, row.attempt_count) == ("failed", 5)
