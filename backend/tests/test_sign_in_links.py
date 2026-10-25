"""An outside identity that meets an existing account waits for its owner (services/sign_in/link_requests.py).

What the link must be, after OWASP's Forgot Password Cheat Sheet and ASVS 5.0 V6: random and long, kept
only as a hash, single use, short-lived, tied to the address it went to; following it changes nothing,
only confirming does. Keycloak's first-broker-login flow ("verify existing account by e-mail") links
the same way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.oauth import IdentityLinkRequest, OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
from faden_backend.services.notifications.dispatcher import NotificationDispatcher
from faden_backend.services.sign_in import accounts, link_requests
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from tests.test_account_hardening import _person

pytestmark = pytest.mark.anyio


@pytest.fixture
def by_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "link_confirmation", link_requests.ask_the_owner)


def _identity(user: User, **changes: Any) -> ExternalIdentity:
    values: dict[str, Any] = {
        "provider": "corp", "provider_name": "Corp", "subject": f"sub-{uuid.uuid4().hex[:8]}",
        "email": user.email, "email_trusted": True, "create_account": True,
    }
    return ExternalIdentity(**{**values, **changes})


async def _verified(db: AsyncSession) -> User:
    user = await _person(db)
    user.is_verified = True
    await db.flush()
    return user


async def _mail(db: AsyncSession, user: User) -> NotificationOutbox:
    return (await db.execute(
        select(NotificationOutbox).where(
            NotificationOutbox.kind == "link_confirmation",
            NotificationOutbox.payload_json["to_email"].astext == user.email,
        ),
    )).scalar_one()


def _token(mail: NotificationOutbox) -> str:
    return parse_qs(urlsplit(mail.payload_json["confirm_url"]).fragment)["link"][0]


async def _requested(db: AsyncSession) -> tuple[User, ExternalIdentity, str]:
    user = await _verified(db)
    identity = _identity(user)
    assert await accounts.sign_in(db, identity) == Refusal.LINK_CONFIRMATION_SENT
    return user, identity, _token(await _mail(db, user))


# ── The request ─────────────────────────────────────────────────────────


async def test_meeting_an_account_sends_its_owner_a_link(dbsession: AsyncSession, by_mail: None) -> None:
    user, identity, token = await _requested(dbsession)
    mail = await _mail(dbsession, user)
    assert mail.payload_json["provider_name"] == "Corp"
    assert mail.payload_json["identity_email"] == user.email
    assert mail.payload_json["expires_minutes"] == 30
    url = urlsplit(mail.payload_json["confirm_url"])
    assert url.scheme.startswith("http") and url.path == "/login"
    assert not url.query and url.fragment.startswith("link="), "in the fragment: never sent, never logged"
    assert len(token) >= 43, "256 random bits"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None, "nothing linked yet"


async def test_only_a_hash_of_the_token_is_kept(dbsession: AsyncSession, by_mail: None) -> None:
    _, _, token = await _requested(dbsession)
    row = (await dbsession.execute(select(IdentityLinkRequest))).scalars().all()[-1]
    assert row.token_hash != token and token not in row.token_hash and len(row.token_hash) == 64


async def test_trying_again_soon_sends_no_second_mail(dbsession: AsyncSession, by_mail: None) -> None:
    user = await _verified(dbsession)
    identity = _identity(user)
    for _ in range(3):
        assert await accounts.sign_in(dbsession, identity) == Refusal.LINK_CONFIRMATION_SENT
    mails = (await dbsession.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "link_confirmation",
        NotificationOutbox.payload_json["to_email"].astext == user.email,
    ))).scalars().all()
    assert len(mails) == 1


async def test_without_the_extension_the_sign_in_is_simply_refused(dbsession: AsyncSession) -> None:
    user = await _verified(dbsession)
    assert await accounts.sign_in(dbsession, _identity(user)) == Refusal.LINK_NEEDS_CONFIRMATION


# ── Following and confirming the link ───────────────────────────────────


async def test_the_owner_confirms_and_the_next_sign_in_lands_in_their_account(
    client: AsyncClient, dbsession: AsyncSession, by_mail: None,
) -> None:
    user, identity, token = await _requested(dbsession)
    preview = await client.post("/api/auth/sso/link/preview", json={"token": token})
    assert preview.status_code == 200, preview.text
    assert preview.json()["provider_name"] == "Corp" and preview.json()["account_email"] == user.email
    confirmed = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert confirmed.status_code == 204, confirmed.text
    link = await accounts.find_link(dbsession, "corp", identity.subject)
    assert link is not None and link.user_id == user.id
    signed = await accounts.sign_in(dbsession, identity)
    assert not isinstance(signed, Refusal) and signed.user.id == user.id and not signed.created
    event = await dbsession.scalar(select(AuditEvent).where(
        AuditEvent.action == "auth.identity.link", AuditEvent.target_id == str(user.id),
    ))
    assert event is not None and "email_confirmation" in (event.metadata_json or "")


async def test_following_the_link_uses_nothing_up(
    client: AsyncClient, dbsession: AsyncSession, by_mail: None,
) -> None:
    """A mail scanner that opens links (and so previews) does not spend the token."""
    _, _, token = await _requested(dbsession)
    for _ in range(3):
        assert (await client.post("/api/auth/sso/link/preview", json={"token": token})).status_code == 200
    assert (await client.post("/api/auth/sso/link/confirm", json={"token": token})).status_code == 204


async def test_a_link_works_once(client: AsyncClient, dbsession: AsyncSession, by_mail: None) -> None:
    _, _, token = await _requested(dbsession)
    assert (await client.post("/api/auth/sso/link/confirm", json={"token": token})).status_code == 204
    again = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert again.status_code == 400 and again.json()["detail"] == "sign_in_expired"
    assert (await client.post("/api/auth/sso/link/preview", json={"token": token})).status_code == 404


async def test_an_expired_link(client: AsyncClient, dbsession: AsyncSession, by_mail: None) -> None:
    """30 minutes."""
    _, identity, token = await _requested(dbsession)
    await dbsession.execute(update(IdentityLinkRequest).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert (await client.post("/api/auth/sso/link/preview", json={"token": token})).status_code == 404
    expired = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert expired.status_code == 400 and expired.json()["detail"] == "sign_in_expired"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_a_made_up_token(client: AsyncClient, by_mail: None) -> None:
    made_up = {"token": "x" * 43}
    assert (await client.post("/api/auth/sso/link/preview", json=made_up)).status_code == 404
    assert (await client.post("/api/auth/sso/link/confirm", json=made_up)).status_code == 400


async def test_the_link_is_for_the_address_it_went_to(
    client: AsyncClient, dbsession: AsyncSession, by_mail: None,
) -> None:
    """The account's address changed since the mail: the link proves nothing about the new one."""
    user, identity, token = await _requested(dbsession)
    user.email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert refused.status_code == 400 and refused.json()["detail"] == "not_allowed"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_a_suspended_account_gets_no_link(
    client: AsyncClient, dbsession: AsyncSession, by_mail: None,
) -> None:
    user, identity, token = await _requested(dbsession)
    user.is_active = False
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert refused.status_code == 400 and refused.json()["detail"] == "account_disabled"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_an_identity_linked_elsewhere_meanwhile(
    client: AsyncClient, dbsession: AsyncSession, by_mail: None,
) -> None:
    _, identity, token = await _requested(dbsession)
    other = await _person(dbsession)
    dbsession.add(OAuthAccount(id=uuid.uuid4(), user_id=other.id, oauth_name="corp",
                               account_id=identity.subject, access_token=""))
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/confirm", json={"token": token})
    assert refused.status_code == 400 and refused.json()["detail"] == "not_allowed"


# ── The mail ────────────────────────────────────────────────────────────


async def test_the_dispatcher_sends_the_link_mail(
    dbsession: AsyncSession, by_mail: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from faden_backend.settings import settings

    user, _, _ = await _requested(dbsession)
    sent: list[dict[str, Any]] = []

    class Mailer:
        async def send_link_confirmation(self, payload: dict[str, Any]) -> None:
            """Records the mail."""
            sent.append(payload)

    class Session:
        """The test's own session, for the dispatcher's short ones."""

        async def __aenter__(self) -> AsyncSession:
            return dbsession

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(settings, "mailer_enabled", True)
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    dispatcher = NotificationDispatcher(Session, Mailer())  # type: ignore[arg-type]
    await dispatcher._deliver(await _mail(dbsession, user))  # noqa: SLF001 - one row, without the poll loop
    assert [payload["to_email"] for payload in sent] == [user.email]
