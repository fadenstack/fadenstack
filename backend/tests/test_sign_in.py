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

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User
from faden_backend.services import provisioning
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
        #: What the module offers; none, as without a licence, when a test empties it.
        self.offered: list[dict[str, Any]] = [
            {"key": "corp", "name": "Corp", "kind": "redirect"},
            {"key": "saml", "name": "Corp SAML", "kind": "redirect", "posts_back": True},
            {"key": "dir", "name": "Directory", "kind": "password"},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911 -- one answer per route
        """Answer *request* as the module would."""
        if self.down:
            raise httpx.ConnectError("down", request=request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path == "/api/v1/providers/public":
            return httpx.Response(200, json=self.offered)
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
        if path == "/api/v1/sso-only":
            return httpx.Response(200, json={"domains": ["corp.example"]})
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
    started = await client.get("/api/auth/sso/corp/start", follow_redirects=False)
    assert started.headers["location"] == "/login?error=not_offered"


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
    claims = {"aud": "faden-sso-binding", "key": "corp", "tx": "x", "exp": 2**31}
    forged = jwt.encode(claims, "a guessed key, as long as HS256 wants one", "HS256")
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
    started = await client.get("/api/auth/sso/corp/start", follow_redirects=False)
    assert started.headers["location"] == "/login?error=provider_unavailable"
    directory = await client.post("/api/auth/sso/dir/password", json={"username": "b", "password": "c"})
    assert (directory.status_code, directory.json()["detail"]) == (503, "provider_unavailable")
    assert (await client.get("/api/auth/sso/saml/metadata")).status_code == 503


@pytest.mark.anyio
async def test_a_provider_no_longer_offered(client: AsyncClient, fake: FakeModule) -> None:
    """Removed, switched off, or none offered (the licence) after the page was opened: the page says so."""
    started, transaction = await _start(client)
    saml_started, saml_transaction = await _start(client, "saml")
    fake.offered = []
    gone = "/login?error=not_offered"
    assert (await client.get("/api/auth/sso/corp/start", follow_redirects=False)).headers["location"] == gone
    answer = await client.get(
        f"/api/auth/sso/corp/callback?code=c&state={transaction}",
        cookies={"faden_sso": started.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert answer.headers["location"] == gone
    posted = await client.post(
        "/api/auth/sso/saml/acs", data={"SAMLResponse": "PHNhbWw+", "RelayState": saml_transaction},
        cookies={"faden_sso": saml_started.cookies["faden_sso"]}, follow_redirects=False,
    )
    assert posted.headers["location"] == gone
    assert fake.finished == [], "nothing reaches the module for a provider it no longer offers"
    directory = await client.post("/api/auth/sso/dir/password", json={"username": "b", "password": "c"})
    assert (directory.status_code, directory.json()["detail"]) == (404, "not_offered")
    assert (await client.get("/api/auth/sso/saml/metadata")).status_code == 404


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
async def test_without_manage_groups_the_rules_still_add(dbsession: AsyncSession) -> None:
    """The rules' groups are the provider's answer; manage_groups decides only whether stale ones go."""
    team, other = await _group(dbsession), await _group(dbsession)
    signed = await accounts.sign_in(dbsession, _as_identity(subject="s-8", group_ids=[team.id]))
    assert isinstance(signed, accounts.SignedIn) and signed.groups_added == [team.id]
    again = await accounts.sign_in(dbsession, _as_identity(subject="s-8", group_ids=[other.id]))
    assert isinstance(again, accounts.SignedIn)
    assert again.groups_added == [other.id] and again.groups_removed == []
    member_of = set(await dbsession.scalars(select(UserGroup.group_id).where(UserGroup.user_id == signed.user.id)))
    assert member_of == {team.id, other.id}


@pytest.mark.anyio
async def test_a_new_account_gets_the_roles_named_and_no_others(dbsession: AsyncSession) -> None:
    """Denied by default: no roles named, none given (the console names ``default_user`` for a new provider)."""
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    chat = await rbac.get_role_by_name("default_user")
    named = await accounts.sign_in(dbsession, _as_identity(default_role_ids=[chat.id]))
    bare = await accounts.sign_in(dbsession, _as_identity(default_role_ids=[]))
    assert isinstance(named, accounts.SignedIn) and isinstance(bare, accounts.SignedIn)
    assert [r.name for r in await rbac.get_user_roles(named.user.id)] == ["default_user"]
    assert await rbac.get_user_roles(bare.user.id) == []


# ── People the provider provisions ──────────────────────────────────────


async def _provisioned(db: AsyncSession, *, external_id: str | None = None, email: str | None = None) -> User:
    """A person the provider "corp" provisioned (round 3: SCIM), never signed in yet."""
    tag = uuid.uuid4().hex[:8]
    email = email or f"p-{tag}@corp.example"
    person = provisioning.Person(external_id=external_id or f"ext-{tag}", login=email, email=email)
    found, _ = await provisioning.put_person(db, provisioning.source_of("corp"), person)
    return found.user


@pytest.mark.anyio
async def test_a_provisioned_person_is_linked_by_subject(dbsession: AsyncSession) -> None:
    """Decision 3: the provider names the person with the ID its provisioning sent; no proof is asked."""
    made = await _provisioned(dbsession, external_id="oid-1")
    signed = await accounts.sign_in(
        dbsession, _as_identity(subject="oid-1", email="elsewhere@corp.example", email_trusted=False),
    )
    assert isinstance(signed, accounts.SignedIn) and signed.user.id == made.id
    assert signed.user.email == made.email, "the provisioning keeps the address"
    assert await accounts.find_link(dbsession, "corp", "oid-1") is not None
    again = await accounts.sign_in(dbsession, _as_identity(subject="oid-1", email="elsewhere@corp.example"))
    assert isinstance(again, accounts.SignedIn) and again.user.email == made.email


@pytest.mark.anyio
async def test_else_by_the_trusted_address_and_only_from_the_same_provider(dbsession: AsyncSession) -> None:
    made = await _provisioned(dbsession)
    untrusted = await accounts.sign_in(dbsession, _as_identity(subject="okta-1", email=made.email, email_trusted=False))
    assert untrusted is Refusal.EMAIL_NOT_TRUSTED
    other = await accounts.sign_in(dbsession, _as_identity(provider="other", subject="x-1", email=made.email))
    assert other is Refusal.LINK_NEEDS_CONFIRMATION, "another provider did not provision them"
    signed = await accounts.sign_in(dbsession, _as_identity(subject="okta-1", email=made.email))
    assert isinstance(signed, accounts.SignedIn) and signed.user.id == made.id


@pytest.mark.anyio
async def test_a_provisioned_person_the_provider_suspended_stays_out(dbsession: AsyncSession) -> None:
    made = await _provisioned(dbsession, external_id="oid-2")
    await provisioning.remove_person(dbsession, provisioning.source_of("corp"), made.id)
    refused = await accounts.sign_in(dbsession, _as_identity(subject="oid-2", email=made.email))
    assert refused is Refusal.ACCOUNT_DISABLED


@pytest.mark.anyio
async def test_the_provisioning_keeps_the_memberships(dbsession: AsyncSession) -> None:
    """The sign-in's group answer changes nothing for a provisioned person: the provisioning is the source."""
    source = provisioning.source_of("corp")
    made = await _provisioned(dbsession, external_id="oid-3")
    team, _ = await provisioning.put_team(dbsession, source, provisioning.Team(name=f"t-{uuid.uuid4().hex[:8]}"))
    await provisioning.change_members(dbsession, source, team.id, add=[made.id])
    rule = await _group(dbsession)
    signed = await accounts.sign_in(
        dbsession, _as_identity(subject="oid-3", group_ids=[rule.id], manage_groups=True),
    )
    assert isinstance(signed, accounts.SignedIn)
    assert signed.groups_added == [] and signed.groups_removed == []
    member_of = set(await dbsession.scalars(select(UserGroup.group_id).where(UserGroup.user_id == made.id)))
    assert member_of == {team.id}


@pytest.mark.anyio
async def test_provisioned_only_lets_in_the_provisioned_and_superusers(dbsession: AsyncSession) -> None:
    strict = {"provisioned_only": True}
    refused = await accounts.sign_in(dbsession, _as_identity(subject="new-1", **strict))
    assert refused is Refusal.NOT_PROVISIONED, "nobody gets an account by signing in"
    assert await accounts.find_link(dbsession, "corp", "new-1") is None

    linked = await accounts.sign_in(dbsession, _as_identity(subject="old-1"))
    assert isinstance(linked, accounts.SignedIn)
    assert await accounts.sign_in(dbsession, _as_identity(subject="old-1", **strict)) is Refusal.NOT_PROVISIONED

    person = await _person(dbsession)
    by_address = await accounts.sign_in(dbsession, _as_identity(email=person.email, **strict))
    assert by_address is Refusal.NOT_PROVISIONED

    admin = await _person(dbsession, admin=True)
    unlinked_admin = await accounts.sign_in(dbsession, _as_identity(email=admin.email, **strict))
    assert unlinked_admin is Refusal.LINK_NEEDS_CONFIRMATION, "a superuser may still prove the account"
    await accounts.link(dbsession, admin, _as_identity(subject="admin-1", email=admin.email))
    assert isinstance(await accounts.sign_in(dbsession, _as_identity(subject="admin-1", **strict)), accounts.SignedIn)

    made = await _provisioned(dbsession, external_id="oid-4")
    signed = await accounts.sign_in(dbsession, _as_identity(subject="oid-4", **strict))
    assert isinstance(signed, accounts.SignedIn) and signed.user.id == made.id


@pytest.mark.anyio
async def test_the_sso_only_domains_from_the_module(fake: FakeModule) -> None:
    assert (await module.sso_only()).domains == ["corp.example"]
    fake.down = True
    with pytest.raises(module.ModuleUnavailableError):
        await module.sso_only()


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
async def test_a_checks_refusal_says_nothing_about_the_password_or_the_account(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked before the password counts: a right password, a wrong one, no account, a superuser's typo."""
    person, admin = await _person(dbsession), await _person(dbsession, admin=True)
    asked: list[str] = []

    async def provider_only(email: str) -> str | None:
        asked.append(email)
        return "signs in with its provider" if email.endswith("@example.com") else None

    monkeypatch.setattr(registry, "password_sign_in_checks", [provider_only])
    tries = [
        (person.email, PASSWORD), (person.email, "not-the-password"),
        ("nobody@example.com", "not-the-password"), (admin.email, "not-the-password"),
    ]
    for username, password in tries:
        r = await client.post("/api/auth/jwt/login", data={"username": username, "password": password})
        assert (r.status_code, r.json()["detail"]) == (400, "LOGIN_USE_SIGN_IN_PROVIDER"), username
    assert asked == [username for username, _ in tries]
    elsewhere = await client.post("/api/auth/jwt/login", data={"username": "nobody@elsewhere.example", "password": "x"})
    assert (elsewhere.status_code, elsewhere.json()["detail"]) == (400, "LOGIN_BAD_CREDENTIALS")


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
