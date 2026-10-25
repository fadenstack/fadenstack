"""Mails go out in the recipient's language (services/notifications/language.py).

Their own pick (preferences, ``language``) first, else what their browser asked for when they set the mail
off, else none: the mail service then takes its default. The mail service has every mail in each of the
console's languages.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.user_preferences import UserPreference
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
from faden_backend.services.notifications.language import recipient_language
from faden_backend.services.sign_in import accounts, link_requests
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from tests.test_account_hardening import _person

pytestmark = pytest.mark.anyio


async def _speaks(db: AsyncSession, user: User, language: str) -> None:
    db.add(UserPreference(user_id=user.id, preferences={"language": language}))
    await db.flush()


async def _mail(db: AsyncSession, kind: str, to: str) -> NotificationOutbox:
    return (await db.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == kind, NotificationOutbox.payload_json["to_email"].astext == to,
    ))).scalar_one()


async def test_their_own_pick_first(dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    await _speaks(dbsession, user, "de-AT")
    assert await recipient_language(dbsession, user.id, "fr-FR,fr;q=0.9") == "de-AT"


@pytest.mark.parametrize(("header", "language"), [
    ("fr-FR,fr;q=0.9,en;q=0.8", "fr-FR"),
    ("zh-Hans", "zh-Hans"),
    ("*;q=0.5", None),
    ("", None),
    (None, None),
])
async def test_else_what_the_browser_asked_for(
    dbsession: AsyncSession, header: str | None, language: str | None,
) -> None:
    user = await _person(dbsession)
    assert await recipient_language(dbsession, user.id, header) == language


async def test_the_password_reset_in_the_browsers_language(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    sent = await client.post(
        "/api/auth/forgot-password", json={"email": user.email}, headers={"Accept-Language": "es-ES,es;q=0.9"},
    )
    assert sent.status_code == 202, sent.text
    assert (await _mail(dbsession, "password_reset", user.email)).payload_json["language"] == "es-ES"


async def test_the_password_reset_in_their_own_language(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    await _speaks(dbsession, user, "zh")
    sent = await client.post(
        "/api/auth/forgot-password", json={"email": user.email}, headers={"Accept-Language": "en"},
    )
    assert sent.status_code == 202, sent.text
    assert (await _mail(dbsession, "password_reset", user.email)).payload_json["language"] == "zh"


async def test_the_link_confirmation_in_their_language(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "link_confirmation", link_requests.ask_the_owner)
    user = await _person(dbsession)
    user.is_verified = True
    await _speaks(dbsession, user, "fr")
    identity = ExternalIdentity(
        provider="corp", provider_name="Corp", subject=f"s-{uuid.uuid4().hex[:8]}", email=user.email,
        email_trusted=True, create_account=True,
    )
    assert await accounts.sign_in(dbsession, identity) == Refusal.LINK_CONFIRMATION_SENT
    mail = (await _mail(dbsession, "link_confirmation", user.email)).payload_json
    assert mail["language"] == "fr" and mail["expires_minutes"] == 10
