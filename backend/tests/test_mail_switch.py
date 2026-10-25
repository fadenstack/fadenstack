"""Core sends no mail; a registered sender does (extensions.mail_sender), and the outbox expires.

Nothing may depend on mail in core: without a sender nothing is queued, the sign-in page offers no reset by
mail, and the forgotten-password route still answers the same for every address (ASVS 5.0 6.3.8). A message
not sent by its expiry is deleted, never sent late.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.notifications import NotificationAlertState, NotificationOutbox
from faden_backend.services.extensions import mail_available, registry
from faden_backend.services.notifications import NotificationService
from faden_backend.services.notifications.dispatcher import NotificationDispatcher
from tests.conftest import RecordingMailSender
from tests.test_account_hardening import _person

pytestmark = pytest.mark.anyio


async def _resets(db: AsyncSession, to: str) -> list[NotificationOutbox]:
    return list((await db.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "password_reset", NotificationOutbox.payload_json["to_email"].astext == to,
    ))).scalars())


async def test_core_sends_no_mail(client: AsyncClient, dbsession: AsyncSession) -> None:
    assert registry.mail_sender is None and not mail_available()
    user = await _person(dbsession)
    sent = await client.post("/api/auth/forgot-password", json={"email": user.email})
    assert sent.status_code == 202, "the same answer for every address"
    assert await _resets(dbsession, user.email) == [], "nothing queued for a mailer that may never come"
    assert (await client.get("/api/auth/options")).json()["password_reset"] is False


async def test_with_a_sender_the_reset_mail_expires_with_its_link(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    user = await _person(dbsession)
    assert (await client.get("/api/auth/options")).json()["password_reset"] is True
    before = datetime.now(UTC)
    assert (await client.post("/api/auth/forgot-password", json={"email": user.email})).status_code == 202
    (mail,) = await _resets(dbsession, user.email)
    assert before + timedelta(minutes=59) < mail.expires_at <= datetime.now(UTC) + timedelta(hours=1)


async def test_a_sender_that_is_off_or_cannot_say(client: AsyncClient, mail_on: RecordingMailSender) -> None:
    mail_on.on = False
    assert not mail_available()
    assert (await client.get("/api/auth/options")).json()["password_reset"] is False

    def broken() -> bool:
        raise RuntimeError("no answer")

    mail_on.available = broken  # type: ignore[method-assign]
    assert not mail_available()


async def test_administrators_alerts_need_mail_too(dbsession: AsyncSession) -> None:
    fingerprint = f"test:{uuid.uuid4().hex[:8]}"
    queued = await NotificationService(dbsession).maybe_enqueue_admin_alert(
        subject="s", severity="warning", fingerprint=fingerprint, summary="x", details="y", source="test",
    )
    assert queued is False
    state = await dbsession.scalar(select(NotificationAlertState).where(
        NotificationAlertState.fingerprint == fingerprint,
    ))
    assert state is None, "no cooldown starts for an alert that was never queued"


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


async def _outbox(db: AsyncSession) -> dict[str, NotificationOutbox]:
    now = datetime.now(UTC)
    rows = {
        "due": NotificationOutbox(kind="password_reset", payload_json={"to_email": "due@example.com"},
                                  status="pending", expires_at=now + timedelta(minutes=5)),
        "stale": NotificationOutbox(kind="password_reset", payload_json={"to_email": "stale@example.com"},
                                    status="pending", expires_at=now - timedelta(seconds=1)),
        "failed": NotificationOutbox(kind="link_code", payload_json={"to_email": "failed@example.com"},
                                     status="failed", expires_at=now - timedelta(seconds=1)),
        "sent": NotificationOutbox(kind="link_code", payload_json={"to_email": "sent@example.com"},
                                   status="sent", sent_at=now, expires_at=now - timedelta(seconds=1)),
    }
    db.add_all(rows.values())
    await db.flush()
    return rows


async def test_the_dispatcher_hands_mail_to_the_sender_and_deletes_what_expired(
    dbsession: AsyncSession, mail_on: RecordingMailSender, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = await _outbox(dbsession)
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001
    ours = {"due@example.com", "stale@example.com", "failed@example.com", "sent@example.com"}
    sent = [(kind, payload["to_email"]) for kind, payload in mail_on.sent if payload.get("to_email") in ours]
    assert sent == [("password_reset", "due@example.com")]
    left = {r.payload_json.get("to_email") for r in (await dbsession.execute(select(NotificationOutbox))).scalars()}
    assert {"due@example.com", "sent@example.com"} <= left, "sent ones go after a week (retention)"
    assert not {"stale@example.com", "failed@example.com"} & left, "expired unsent: deleted, never sent late"
    await dbsession.refresh(rows["due"])
    assert rows["due"].status == "sent"


async def test_without_a_sender_the_dispatcher_only_deletes_what_expired(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _outbox(dbsession)
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    await NotificationDispatcher(_Session(dbsession))._poll_once()  # type: ignore[arg-type]  # noqa: SLF001
    rows = (await dbsession.execute(select(NotificationOutbox))).scalars()
    left = {r.payload_json.get("to_email"): r.status for r in rows}
    assert left.get("due@example.com") == "pending" and "stale@example.com" not in left
