"""Signing in with an outside provider through a sign-in module (services/sign_in, web/api/auth_sso).

A fake module answers over httpx's mock transport, so the contract, the service token and the browser's
side are checked together; the account rules are checked on ``accounts.sign_in`` directly.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
from faden_backend.services.sign_in import accounts, module
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from faden_backend.settings import settings
from tests.test_account_hardening import PASSWORD, _person
from tests.test_api_tokens import _As, signed_in  # noqa: F401 - fixture used by name

TOKEN = "module-token"  # noqa: S105


class FakeModule:
    """The module's side of the contract, with the identity a test wants it to give."""

    def __init__(self) -> None:
        self.identity: dict[str, Any] | None = None
        self.refusal: str | None = None
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.admin_calls: list[tuple[str, str, Any, dict[str, str]]] = []
        self.down = False
        self.start_refusal: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911 -- one answer per route
        """Answer *request* as the module would."""
        if self.down:
            raise httpx.ConnectError("down", request=request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == "/api/v1/providers/public":
            return httpx.Response(200, json=[
                {"key": "corp", "name": "Corp", "kind": "redirect"},
                {"key": "saml", "name": "Corp SAML", "kind": "redirect", "posts_back": True},
                {"key": "dir", "name": "Directory", "kind": "password"},
            ])
        if path == "/api/v1/sign-in/start":
            if self.start_refusal:
                return httpx.Response(403, json={"detail": self.start_refusal})
            transaction = f"tx-{uuid.uuid4().hex}"
            self.started.append({**body, "transaction": transaction})
            return httpx.Response(200, json={
                "redirect_url": f"https://idp.example/authorize?state={transaction}", "transaction": transaction,
            })
        if path in ("/api/v1/sign-in/finish", "/api/v1/sign-in/password"):
            self.finished.append(body)
            if path.endswith("password") and body["password"] != "right":  # noqa: S105
                return httpx.Response(200, json={"outcome": "refused", "refusal": "wrong_credentials"})
            if self.refusal:
                return httpx.Response(200, json={"outcome": "refused", "refusal": self.refusal})
            return httpx.Response(200, json={"outcome": "identity", "identity": self.identity})
        if path == "/api/v1/providers/saml/metadata":
            return httpx.Response(200, content=b"<EntityDescriptor/>", headers={"content-type": "application/xml"})
        if path.startswith("/api/v1/admin/providers"):
            self.admin_calls.append((request.method, path, body, dict(request.url.params)))
            if path.endswith("/missing"):
                return httpx.Response(404, json={"detail": "No such provider"})
            return httpx.Response(200, json={"seen": request.method})
        return httpx.Response(404, json={"detail": "unknown"})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeModule:
    fake = FakeModule()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_service_url", "http://auth.internal")
    monkeypatch.setattr(settings, "auth_service_token", TOKEN)
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(module, "_http", lambda: client)
    return fake


def _identity(**changes: Any) -> dict[str, Any]:
    subject = changes.pop("subject", f"sub-{uuid.uuid4().hex[:8]}")
    base = {
        "provider": "corp", "provider_name": "Corp", "subject": subject,
        "email": f"{subject}@corp.example", "email_trusted": True, "create_account": True,
    }
    return {**base, **changes}


async def _start(client: AsyncClient, key: str = "corp") -> tuple[httpx.Response, str]:
    started = await client.get(f"/api/auth/sso/{key}/start", follow_redirects=False)
    assert started.status_code == 302, started.text
    return started, started.headers["location"].split("state=")[1]


# ── The browser's side ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_without_a_module_there_is_nothing_to_sign_in_with(client: AsyncClient) -> None:
    assert settings.auth_enabled is False
    assert (await client.get("/api/auth/sso/providers")).json() == []
    assert (await client.get("/api/auth/sso/corp/start", follow_redirects=False)).status_code == 404


@pytest.mark.anyio
async def test_providers_for_the_sign_in_page(client: AsyncClient, fake: FakeModule) -> None:
    providers = (await client.get("/api/auth/sso/providers")).json()
    assert [(p["key"], p["kind"]) for p in providers] == [("corp", "redirect"), ("saml", "redirect"), ("dir", "password")]


@pytest.mark.anyio
async def test_a_redirect_sign_in_from_start_to_signed_in(
    client: AsyncClient, fake: FakeModule, dbsession: AsyncSession,
) -> None:
    started, transaction = await _start(client)
    assert started.headers["location"].startswith("https://idp.example/authorize")
    assert fake.started[-1]["return_url"] == "http://test/api/auth/sso/corp/callback"
    binding = started.cookies.get("faden_sso")
    assert binding and "httponly" in started.headers["set-cookie"].lower()
    assert "path=/api/auth/sso" in started.headers["set-cookie"].lower()

    fake.identity = _identity(subject="alice", email="alice@corp.example")
    done = await client.get(
        f"/api/auth/sso/corp/callback?code=abc&state={transaction}", cookies={"faden_sso": binding},
        follow_redirects=False,
    )
    assert done.status_code == 302 and done.headers["location"] == "/"
    assert fake.finished[-1]["params"] == {"code": "abc", "state": transaction}
    assert done.cookies.get("fapiauth")
    user = (await dbsession.execute(select(User).where(User.email == "alice@corp.example"))).scalar_one()
    me = await client.get("/api/users/me", cookies={"fapiauth": done.cookies["fapiauth"]})
    assert me.status_code == 200 and me.json()["id"] == str(user.id)


@pytest.mark.anyio
async def test_an_answer_this_browser_did_not_start_is_refused(client: AsyncClient, fake: FakeModule) -> None:
    fake.identity = _identity()
    _, transaction = await _start(client)
    other, other_transaction = await _start(client)
    without = await client.get(f"/api/auth/sso/corp/callback?code=x&state={transaction}", follow_redirects=False)
    assert without.headers["location"] == "/login?error=sign_in_expired"
    crossed = await client.get(
        f"/api/auth/sso/corp/callback?code=x&state={transaction}",
        cookies={"faden_sso": other.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert crossed.headers["location"] == "/login?error=sign_in_expired"
    assert other_transaction != transaction
    assert fake.finished == [], "the module was not even asked"


@pytest.mark.anyio
async def test_a_forged_binding_is_refused(client: AsyncClient, fake: FakeModule) -> None:
    _, transaction = await _start(client)
    forged = jwt.encode({"aud": "faden-sso-binding", "key": "corp", "tx": "x", "exp": 2**31}, "guess", "HS256")
    r = await client.get(
        f"/api/auth/sso/corp/callback?state={transaction}", cookies={"faden_sso": forged}, follow_redirects=False,
    )
    assert r.headers["location"] == "/login?error=sign_in_expired"


@pytest.mark.anyio
async def test_the_modules_refusal_is_passed_on(client: AsyncClient, fake: FakeModule) -> None:
    started, transaction = await _start(client)
    fake.refusal = "not_allowed"
    r = await client.get(
        f"/api/auth/sso/corp/callback?error=access_denied&state={transaction}",
        cookies={"faden_sso": started.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert r.headers["location"] == "/login?error=not_allowed"


@pytest.mark.anyio
async def test_the_modules_reason_for_not_starting_is_passed_on(client: AsyncClient, fake: FakeModule) -> None:
    fake.start_refusal = "license"
    r = await client.get("/api/auth/sso/corp/start", follow_redirects=False)
    assert r.headers["location"] == "/login?error=license"
    fake.start_refusal = "something we never heard of"
    r = await client.get("/api/auth/sso/corp/start", follow_redirects=False)
    assert r.headers["location"] == "/login?error=provider_error"


@pytest.mark.anyio
async def test_a_module_that_is_down(client: AsyncClient, fake: FakeModule) -> None:
    fake.down = True
    assert (await client.get("/api/auth/sso/providers")).json() == []
    assert (await client.get("/api/auth/sso/corp/start", follow_redirects=False)).status_code == 404


@pytest.mark.anyio
async def test_no_token_no_call(client: AsyncClient, fake: FakeModule, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_service_token", "")
    assert (await client.get("/api/auth/sso/providers")).json() == []


@pytest.mark.anyio
async def test_a_posted_answer_signs_in(client: AsyncClient, fake: FakeModule) -> None:
    started, transaction = await _start(client, "saml")
    assert fake.started[-1]["return_url"] == "http://test/api/auth/sso/saml/acs"
    fake.identity = _identity(provider="saml")
    done = await client.post(
        "/api/auth/sso/saml/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": transaction},
        cookies={"faden_sso": started.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert done.status_code == 302 and done.headers["location"] == "/"
    assert fake.finished[-1]["params"]["SAMLResponse"] == "PHNhbWw+"


@pytest.mark.anyio
async def test_posted_answers_need_https_outside_development(
    client: AsyncClient, fake: FakeModule, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "prod")
    r = await client.get("/api/auth/sso/saml/start", follow_redirects=False)
    assert r.headers["location"] == "/login?error=needs_https"
    over_https = await client.get(
        "/api/auth/sso/saml/start", headers={"x-forwarded-proto": "https"}, follow_redirects=False,
    )
    assert over_https.status_code == 302
    cookie = over_https.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "secure" in cookie
    assert fake.started[-1]["return_url"] == "https://test/api/auth/sso/saml/acs"


@pytest.mark.anyio
async def test_a_directory_password(client: AsyncClient, fake: FakeModule) -> None:
    fake.identity = _identity(provider="dir")
    wrong = await client.post("/api/auth/sso/dir/password", json={"username": "bob", "password": "nope"})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "wrong_credentials"
    right = await client.post("/api/auth/sso/dir/password", json={"username": "bob", "password": "right"})
    assert right.status_code == 204 and right.cookies.get("fapiauth")
    assert (await client.post("/api/auth/sso/corp/password", json={"username": "b", "password": "c"})).status_code == 404


@pytest.mark.anyio
async def test_the_sign_in_lasts_no_longer_than_the_provider_allows(client: AsyncClient, fake: FakeModule) -> None:
    started, transaction = await _start(client)
    fake.identity = _identity(session_lifetime_s=300)
    done = await client.get(
        f"/api/auth/sso/corp/callback?state={transaction}", cookies={"faden_sso": started.cookies["faden_sso"]},
        follow_redirects=False,
    )
    claims = jwt.decode(done.cookies["fapiauth"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 300
    assert "max-age=300" in done.headers["set-cookie"].lower()


@pytest.mark.anyio
async def test_service_provider_metadata(client: AsyncClient, fake: FakeModule) -> None:
    r = await client.get("/api/auth/sso/saml/metadata")
    assert r.status_code == 200 and r.content == b"<EntityDescriptor/>"


@pytest.mark.anyio
async def test_keys_are_plain_names(client: AsyncClient, fake: FakeModule) -> None:
    assert (await client.get("/api/auth/sso/..%2Fadmin/start", follow_redirects=False)).status_code in (404, 422)
    assert (await client.get("/api/auth/sso/UPPER/start", follow_redirects=False)).status_code == 422


# ── Provider settings, for administrators ───────────────────────────────


@pytest.mark.anyio
async def test_settings_are_forwarded_for_administrators(
    client: AsyncClient, fake: FakeModule, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    signed_in.user = await _person(dbsession, admin=True)
    r = await client.patch("/api/admin/auth-providers/corp?x=1", json={"name": "Corp AD"})
    assert r.status_code == 200 and r.json() == {"seen": "PATCH"}
    assert fake.admin_calls[-1] == ("PATCH", "/api/v1/admin/providers/corp", {"name": "Corp AD"}, {"x": "1"})
    assert (await client.get("/api/admin/auth-providers/")).status_code == 200
    assert fake.admin_calls[-1][1] == "/api/v1/admin/providers"
    missing = await client.get("/api/admin/auth-providers/missing")
    assert missing.status_code == 404 and missing.json()["detail"] == "No such provider"


@pytest.mark.anyio
async def test_settings_are_not_for_others(
    client: AsyncClient, fake: FakeModule, signed_in: _As, dbsession: AsyncSession,  # noqa: F811
) -> None:
    signed_in.user = await _person(dbsession)
    assert (await client.get("/api/admin/auth-providers/")).status_code == 403
    assert fake.admin_calls == []


# ── Identity to account ─────────────────────────────────────────────────


def _as_identity(**changes: Any) -> ExternalIdentity:
    return ExternalIdentity.model_validate(_identity(**changes))


@pytest.mark.anyio
async def test_a_new_person_gets_an_account_and_is_found_again_by_subject(dbsession: AsyncSession) -> None:
    first = await accounts.sign_in(dbsession, _as_identity(subject="s-1", email="one@corp.example"))
    assert isinstance(first, accounts.SignedIn) and first.created
    assert first.user.is_verified and not first.user.is_superuser
    again = await accounts.sign_in(dbsession, _as_identity(subject="s-1", email="renamed@corp.example"))
    assert isinstance(again, accounts.SignedIn) and not again.created
    assert again.user.id == first.user.id
    assert again.user.email == "renamed@corp.example", "the provider's address is followed"
    link = await accounts.find_link(dbsession, "corp", "s-1")
    assert link is not None and link.last_sign_in_at is not None


@pytest.mark.anyio
async def test_an_existing_account_is_not_taken_over_by_its_address(dbsession: AsyncSession) -> None:
    person = await _person(dbsession, admin=True)
    refused = await accounts.sign_in(dbsession, _as_identity(email=person.email))
    assert refused is Refusal.LINK_NEEDS_CONFIRMATION
    links = (await dbsession.execute(select(OAuthAccount).where(OAuthAccount.user_id == person.id))).scalars().all()
    assert links == []


@pytest.mark.anyio
async def test_an_unverified_or_suspended_account(dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    person.is_verified = False
    await dbsession.flush()
    assert await accounts.sign_in(dbsession, _as_identity(email=person.email)) is Refusal.ACCOUNT_NOT_VERIFIED

    linked = await accounts.sign_in(dbsession, _as_identity(subject="s-2"))
    assert isinstance(linked, accounts.SignedIn)
    linked.user.is_active = False
    await dbsession.flush()
    assert await accounts.sign_in(dbsession, _as_identity(subject="s-2")) is Refusal.ACCOUNT_DISABLED


@pytest.mark.anyio
async def test_what_a_new_account_needs(dbsession: AsyncSession) -> None:
    assert await accounts.sign_in(dbsession, _as_identity(email=None)) is Refusal.EMAIL_MISSING
    assert await accounts.sign_in(dbsession, _as_identity(email_trusted=False)) is Refusal.EMAIL_NOT_TRUSTED
    assert await accounts.sign_in(dbsession, _as_identity(create_account=False)) is Refusal.REGISTRATION_DISABLED


@pytest.mark.anyio
async def test_a_linked_person_signs_in_without_an_address(dbsession: AsyncSession) -> None:
    assert isinstance(await accounts.sign_in(dbsession, _as_identity(subject="s-3")), accounts.SignedIn)
    assert isinstance(await accounts.sign_in(dbsession, _as_identity(subject="s-3", email=None)), accounts.SignedIn)


@pytest.mark.anyio
async def test_an_address_another_account_has_is_not_taken(dbsession: AsyncSession) -> None:
    other = await _person(dbsession)
    first = await accounts.sign_in(dbsession, _as_identity(subject="s-4"))
    assert isinstance(first, accounts.SignedIn)
    again = await accounts.sign_in(dbsession, _as_identity(subject="s-4", email=other.email))
    assert isinstance(again, accounts.SignedIn) and again.user.email != other.email


async def _group(db: AsyncSession) -> Group:
    group = Group(id=uuid.uuid4(), name=f"g-{uuid.uuid4().hex[:8]}")
    db.add(group)
    await db.flush()
    return group


@pytest.mark.anyio
async def test_groups_follow_the_provider_and_leave_the_administrators_alone(dbsession: AsyncSession) -> None:
    sales, staff, manual = await _group(dbsession), await _group(dbsession), await _group(dbsession)
    signed = await accounts.sign_in(
        dbsession, _as_identity(subject="s-5", group_ids=[sales.id, staff.id, uuid.uuid4()], manage_groups=True),
    )
    assert isinstance(signed, accounts.SignedIn)
    assert set(signed.groups_added) == {sales.id, staff.id}, "a group that does not exist is left out"
    dbsession.add(UserGroup(user_id=signed.user.id, group_id=manual.id))
    await dbsession.flush()

    again = await accounts.sign_in(dbsession, _as_identity(subject="s-5", group_ids=[staff.id], manage_groups=True))
    assert isinstance(again, accounts.SignedIn) and again.groups_removed == [sales.id]
    rows = dict((await dbsession.execute(
        select(UserGroup.group_id, UserGroup.source).where(UserGroup.user_id == signed.user.id),
    )).all())
    assert rows == {staff.id: "provider:corp", manual.id: None}


@pytest.mark.anyio
async def test_an_administrators_membership_is_never_taken_back(dbsession: AsyncSession) -> None:
    from faden_backend.db.dao.groups_dao import GroupDAO

    team = await _group(dbsession)
    signed = await accounts.sign_in(dbsession, _as_identity(subject="s-6", group_ids=[team.id], manage_groups=True))
    assert isinstance(signed, accounts.SignedIn)
    await GroupDAO(dbsession).add_members(team.id, [signed.user.id])  # the administrator adds them too
    again = await accounts.sign_in(dbsession, _as_identity(subject="s-6", group_ids=[], manage_groups=True))
    assert isinstance(again, accounts.SignedIn) and again.groups_removed == []


@pytest.mark.anyio
async def test_without_manage_groups_memberships_stay(dbsession: AsyncSession) -> None:
    team = await _group(dbsession)
    signed = await accounts.sign_in(dbsession, _as_identity(subject="s-7", group_ids=[team.id], manage_groups=True))
    assert isinstance(signed, accounts.SignedIn)
    again = await accounts.sign_in(dbsession, _as_identity(subject="s-7", group_ids=[]))
    assert isinstance(again, accounts.SignedIn) and again.groups_removed == []


@pytest.mark.anyio
async def test_the_link_confirmation_extension(dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    person = await _person(dbsession)
    asked: list[uuid.UUID] = []

    async def confirm(session: AsyncSession, user: User, identity: ExternalIdentity) -> Refusal:
        asked.append(user.id)
        return Refusal.LINK_CONFIRMATION_SENT

    monkeypatch.setattr(registry, "link_confirmation", confirm)
    assert await accounts.sign_in(dbsession, _as_identity(email=person.email)) is Refusal.LINK_CONFIRMATION_SENT
    assert asked == [person.id]


# ── Password sign-in checks ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_password_checks_apply_to_all_but_superusers(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    person, admin = await _person(dbsession), await _person(dbsession, admin=True)

    async def provider_only(email: str) -> str | None:
        return "signs in with its provider" if email.endswith("@example.com") else None

    monkeypatch.setattr(registry, "password_sign_in_checks", [provider_only])
    refused = await client.post("/api/auth/jwt/login", data={"username": person.email, "password": PASSWORD})
    assert refused.status_code == 400 and refused.json()["detail"] == "LOGIN_USE_SIGN_IN_PROVIDER"
    allowed = await client.post("/api/auth/jwt/login", data={"username": admin.email, "password": PASSWORD})
    assert allowed.status_code == 200, allowed.text


@pytest.mark.anyio
async def test_a_failing_password_check_refuses(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = await _person(dbsession)

    async def broken(email: str) -> str | None:
        raise RuntimeError("no answer")

    monkeypatch.setattr(registry, "password_sign_in_checks", [broken])
    r = await client.post("/api/auth/jwt/login", data={"username": person.email, "password": PASSWORD})
    assert r.status_code == 400 and r.json()["detail"] == "LOGIN_USE_SIGN_IN_PROVIDER"
