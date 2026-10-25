"""A sign-in that meets an existing account waits for the proof that it is the person's (link_requests.py).

What it must be (ASVS 5.0 7.5.1, V6.3 and V6.6; Keycloak's first-broker-login "verify existing account"):
bound to the browser that signed in (an httpOnly cookie, of which only a hash is kept), short-lived, a few
tries; proved with the account's password, or with a code mailed to the account's address while mail is on.
A mail alone links nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.oauth import IdentityLinkRequest, OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
from faden_backend.services.sign_in import accounts, link_requests
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from tests.conftest import RecordingMailSender
from tests.test_account_hardening import PASSWORD, _person
from tests.test_sign_in import FakeModule, _start, fake  # noqa: F401 - fixture used by name
from tests.test_sign_in import _identity as _module_identity

pytestmark = pytest.mark.anyio


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


async def _waiting(db: AsyncSession) -> tuple[User, ExternalIdentity, str]:
    """An account, an identity with its address that met it, and the token of the browser that waits."""
    user = await _verified(db)
    identity = _identity(user)
    assert await accounts.sign_in(db, identity) == Refusal.LINK_NEEDS_CONFIRMATION
    token = await link_requests.begin(db, identity)
    assert token
    return user, identity, token


def _cookie(token: str) -> dict[str, str]:
    return {link_requests.COOKIE: token}


async def _request(db: AsyncSession, token: str) -> IdentityLinkRequest:
    return (await db.execute(select(IdentityLinkRequest).where(
        IdentityLinkRequest.token_hash == hashlib.sha256(token.encode()).hexdigest(),
    ))).scalar_one()


async def _code(db: AsyncSession, user: User) -> str:
    mails = (await db.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "link_code", NotificationOutbox.payload_json["to_email"].astext == user.email,
    ).order_by(NotificationOutbox.payload_json["requested_at"].astext))).scalars().all()
    return mails[-1].payload_json["code"]  # the latest (created_at is the transaction's time for all)


async def _linked_via(db: AsyncSession, user: User) -> str:
    event = await db.scalar(select(AuditEvent).where(
        AuditEvent.action == "auth.identity.link", AuditEvent.target_id == str(user.id),
    ))
    assert event is not None
    return event.metadata_json or ""


# ── The sign-in waits, in this browser ──────────────────────────────────


async def test_a_directory_sign_in_that_meets_an_account_waits_in_this_browser(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    user = await _verified(dbsession)
    fake.identity = _module_identity(provider="dir", subject="bob", email=user.email)
    met = await client.post("/api/auth/sso/dir/password", json={"username": "bob", "password": "right"})
    assert met.status_code == 400 and met.json()["detail"] == "link_needs_confirmation"
    cookie = met.headers["set-cookie"].lower()
    for part in ("faden_link=", "httponly", "path=/api/auth/sso/link", "max-age=600", "samesite=lax"):
        assert part in cookie, part
    row = await _request(dbsession, met.cookies[link_requests.COOKIE])
    assert row.user_id == user.id and row.identity_json["subject"] == "bob"
    assert await accounts.find_link(dbsession, "dir", "bob") is None, "nothing linked yet"


async def test_a_redirect_sign_in_that_meets_an_account_waits_too(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,  # noqa: F811
) -> None:
    user = await _verified(dbsession)
    started, transaction = await _start(client)
    fake.identity = _module_identity(subject="carol", email=user.email)
    done = await client.get(
        f"/api/auth/sso/corp/callback?code=abc&state={transaction}",
        cookies={"faden_sso": started.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=link_needs_confirmation"
    assert done.cookies.get(link_requests.COOKIE) and not done.cookies.get("fapiauth")


async def test_what_this_browser_waits_for_and_no_other(client: AsyncClient, dbsession: AsyncSession) -> None:
    user, _, token = await _waiting(dbsession)
    seen = await client.get("/api/auth/sso/link", cookies=_cookie(token))
    assert seen.status_code == 200, seen.text
    assert seen.json() | {"expires_at": None} == {
        "provider_name": "Corp", "account_email": user.email, "expires_at": None, "mail": False, "code_sent": False,
    }
    other = await client.get("/api/auth/sso/link")
    assert other.status_code == 404 and other.json()["detail"] == "sign_in_expired"
    proved = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD})
    assert proved.status_code == 400 and proved.json()["detail"] == "sign_in_expired"


async def test_only_a_hash_of_the_token_is_kept(dbsession: AsyncSession) -> None:
    _, _, token = await _waiting(dbsession)
    row = await _request(dbsession, token)
    assert len(token) >= 43, "256 random bits"
    assert token not in row.token_hash and len(row.token_hash) == 64
    assert row.expires_at - row.created_at <= timedelta(minutes=10, seconds=5), "ASVS 5.0 6.5.5"


# ── Proved with the account's password ──────────────────────────────────


async def test_the_accounts_password_links_it_and_signs_in(client: AsyncClient, dbsession: AsyncSession) -> None:
    user, identity, token = await _waiting(dbsession)
    proved = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert proved.status_code == 204, proved.text
    assert proved.cookies.get("fapiauth")
    assert any(
        h.lower().startswith(f"{link_requests.COOKIE}=") and "max-age=0" in h.lower()
        for h in proved.headers.get_list("set-cookie")
    ), "the browser's link cookie is dropped"
    link = await accounts.find_link(dbsession, "corp", identity.subject)
    assert link is not None and link.user_id == user.id
    assert '"via": "password"' in await _linked_via(dbsession, user)
    me = await client.get("/api/users/me", cookies={"fapiauth": proved.cookies["fapiauth"]})
    assert me.status_code == 200 and me.json()["id"] == str(user.id)
    again = await accounts.sign_in(dbsession, identity)
    assert not isinstance(again, Refusal) and again.user.id == user.id and not again.created


async def test_a_wrong_password_counts_and_reads_as_wrong_credentials(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    _, identity, token = await _waiting(dbsession)
    for _ in range(link_requests.MAX_TRIES):
        wrong = await client.post("/api/auth/sso/link/with-password", json={"password": "nope"}, cookies=_cookie(token))
        assert wrong.status_code == 400 and wrong.json()["detail"] == "wrong_credentials"
    right = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert right.status_code == 400 and right.json()["detail"] == "sign_in_expired", "used up"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_the_password_proves_it_in_an_sso_only_domain_too(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It links; it is not a password sign-in, which the domain refuses."""

    async def provider_only(email: str) -> str | None:
        return "signs in with its provider"

    monkeypatch.setattr(registry, "password_sign_in_checks", [provider_only])
    _, _, token = await _waiting(dbsession)
    proved = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert proved.status_code == 204, proved.text


# ── Proved with a code sent to the account's address ────────────────────


async def test_no_code_without_mail(client: AsyncClient, dbsession: AsyncSession) -> None:
    _, _, token = await _waiting(dbsession)
    asked = await client.post("/api/auth/sso/link/code", cookies=_cookie(token))
    assert asked.status_code == 404 and asked.json()["detail"] == "no_mail"
    assert (await dbsession.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "link_code",
    ))).first() is None


async def test_a_code_by_mail_links_it(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    user, identity, token = await _waiting(dbsession)
    assert (await client.get("/api/auth/sso/link", cookies=_cookie(token))).json()["mail"] is True
    asked = await client.post("/api/auth/sso/link/code", cookies=_cookie(token))
    assert asked.status_code == 202, asked.text
    mail = (await dbsession.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "link_code", NotificationOutbox.payload_json["to_email"].astext == user.email,
    ))).scalar_one()
    code = mail.payload_json["code"]
    assert len(code) == 8 and code.isdigit()
    assert mail.payload_json["provider_name"] == "Corp" and mail.payload_json["expires_minutes"] == 10
    assert mail.expires_at == (await _request(dbsession, token)).expires_at, "the mail expires with the code"
    assert (await client.get("/api/auth/sso/link", cookies=_cookie(token))).json()["code_sent"] is True
    proved = await client.post(
        "/api/auth/sso/link/with-code", json={"code": f"{code[:4]} {code[4:]}"}, cookies=_cookie(token),
    )
    assert proved.status_code == 204, proved.text
    assert proved.cookies.get("fapiauth")
    assert (await accounts.find_link(dbsession, "corp", identity.subject)).user_id == user.id
    assert '"via": "email_code"' in await _linked_via(dbsession, user)


async def test_the_code_is_kept_only_as_an_hmac(dbsession: AsyncSession, mail_on: RecordingMailSender) -> None:
    user, _, token = await _waiting(dbsession)
    assert await link_requests.send_code(dbsession, token) == "sent"
    code, row = await _code(dbsession, user), await _request(dbsession, token)
    assert row.code_hash and code not in row.code_hash
    assert row.code_hash != hashlib.sha256(code.encode()).hexdigest(), "keyed: a copy of the table is not enough"


async def test_codes_are_rationed_and_each_replaces_the_last(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    user, _, token = await _waiting(dbsession)

    async def ask() -> tuple[int, str]:
        r = await client.post("/api/auth/sso/link/code", cookies=_cookie(token))
        return r.status_code, r.json().get("detail", "")

    async def a_minute_later() -> None:
        await dbsession.execute(update(IdentityLinkRequest).values(
            code_sent_at=datetime.now(UTC) - link_requests.CODE_EVERY - timedelta(seconds=1),
        ))

    assert (await ask())[0] == 202
    first = await _code(dbsession, user)
    assert await ask() == (429, "too_soon")
    for _ in range(link_requests.MAX_CODES - 1):
        await a_minute_later()
        assert (await ask())[0] == 202
    await a_minute_later()
    assert await ask() == (429, "too_many")
    latest = await _code(dbsession, user)
    if first != latest:
        stale = await client.post("/api/auth/sso/link/with-code", json={"code": first}, cookies=_cookie(token))
        assert stale.status_code == 400 and stale.json()["detail"] == "wrong_credentials"
    proved = await client.post("/api/auth/sso/link/with-code", json={"code": latest}, cookies=_cookie(token))
    assert proved.status_code == 204, proved.text


async def test_wrong_codes_and_passwords_share_the_tries(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    user, identity, token = await _waiting(dbsession)
    await link_requests.send_code(dbsession, token)
    code = await _code(dbsession, user)
    wrong = "00000000" if code != "00000000" else "11111111"
    for i in range(link_requests.MAX_TRIES):
        path, body = ("with-password", {"password": "nope"}) if i % 2 else ("with-code", {"code": wrong})
        r = await client.post(f"/api/auth/sso/link/{path}", json=body, cookies=_cookie(token))
        assert r.status_code == 400 and r.json()["detail"] == "wrong_credentials"
    late = await client.post("/api/auth/sso/link/with-code", json={"code": code}, cookies=_cookie(token))
    assert late.status_code == 400 and late.json()["detail"] == "sign_in_expired"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


# ── When the proof no longer links ──────────────────────────────────────


async def test_an_expired_request(client: AsyncClient, dbsession: AsyncSession) -> None:
    """10 minutes (ASVS 5.0 6.5.5)."""
    _, identity, token = await _waiting(dbsession)
    await dbsession.execute(update(IdentityLinkRequest).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert (await client.get("/api/auth/sso/link", cookies=_cookie(token))).status_code == 404
    late = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert late.status_code == 400 and late.json()["detail"] == "sign_in_expired"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_a_proof_works_once(client: AsyncClient, dbsession: AsyncSession) -> None:
    _, _, token = await _waiting(dbsession)
    body = {"password": PASSWORD}
    assert (await client.post("/api/auth/sso/link/with-password", json=body, cookies=_cookie(token))).status_code == 204
    again = await client.post("/api/auth/sso/link/with-password", json=body, cookies=_cookie(token))
    assert again.status_code == 400 and again.json()["detail"] == "sign_in_expired"


async def test_the_account_must_still_have_the_address(client: AsyncClient, dbsession: AsyncSession) -> None:
    """The account's address changed since the sign-in met it: it may be someone else's account now."""
    user, identity, token = await _waiting(dbsession)
    user.email = f"new-{uuid.uuid4().hex[:6]}@example.com"
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert refused.status_code == 400 and refused.json()["detail"] == "not_allowed"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_a_suspended_account_takes_no_link(client: AsyncClient, dbsession: AsyncSession) -> None:
    user, identity, token = await _waiting(dbsession)
    user.is_active = False
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert refused.status_code == 400 and refused.json()["detail"] == "account_disabled"
    assert await accounts.find_link(dbsession, "corp", identity.subject) is None


async def test_an_identity_linked_elsewhere_meanwhile(client: AsyncClient, dbsession: AsyncSession) -> None:
    _, identity, token = await _waiting(dbsession)
    other = await _person(dbsession)
    dbsession.add(OAuthAccount(id=uuid.uuid4(), user_id=other.id, oauth_name="corp",
                               account_id=identity.subject, access_token=""))
    await dbsession.flush()
    refused = await client.post("/api/auth/sso/link/with-password", json={"password": PASSWORD}, cookies=_cookie(token))
    assert refused.status_code == 400 and refused.json()["detail"] == "not_allowed"


async def test_requests_that_ended_a_day_ago_are_deleted(dbsession: AsyncSession) -> None:
    """They hold the identity's address."""
    _, _, old = await _waiting(dbsession)
    await dbsession.execute(update(IdentityLinkRequest).values(expires_at=datetime.now(UTC) - timedelta(days=2)))
    await _waiting(dbsession)
    left = (await dbsession.execute(select(IdentityLinkRequest.token_hash))).scalars().all()
    assert hashlib.sha256(old.encode()).hexdigest() not in left
