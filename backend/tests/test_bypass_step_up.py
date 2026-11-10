"""The second sign-in before Bypass is switched on (sdk/41; ASVS 5.0 7.5.3), and the settings of 2026-11-09.

The app starts a step-up with its agent token; the page demands a fresh sign-in by the app's own person (the
password again, a provider forced to authenticate anew, the directory password), shows what is approved, and the
app collects a short proof once. Codes and the proof never reach the audit trail. Bypass may be turned off
without Root Mode; turning it on still needs it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi_users.password import PasswordHelper
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.step_up import BypassStepUp, BypassStepUpFailure
from faden_backend.db.models.users import User
from faden_backend.services.agents import step_up
from faden_backend.services.sign_in import module
from faden_backend.services.sign_in.contract import (
    ExternalIdentity,
    ProviderKind,
    ProviderPublic,
    Refusal,
    SignInAnswer,
    SignInStart,
)
from faden_backend.web.api.admin.dependencies import get_root_mode_active
from tests.test_agents import _agent, _As, _grant, _sign_in, _user, secret, signed_in  # noqa: F401 - fixtures

PASSWORD = "A long and right password 7!"  # noqa: S105 - a test account's
START = "/api/agent-tokens/step-ups"
PAGE = "/api/auth/step-up"


async def _person(db: AsyncSession, password: str = PASSWORD) -> User:
    user = await _user(db)
    user.hashed_password = PasswordHelper().hash(password)
    await db.flush()
    return user


async def _app(client: AsyncClient, db: AsyncSession, who: _As, *, device: str = "PC-7") -> tuple[User, dict[str, Any]]:
    """A person with a password, an agent they may use, and the app's sign-in to it on a device."""
    who.user = await _user(db, admin=True)
    slug = f"bypass-{uuid.uuid4().hex[:6]}"
    await _agent(client, slug=slug)
    person = await _person(db)
    await _grant(client, slug, ("user", person.id))
    who.user = person
    tokens = (await _sign_in(client, slug, device)).json()
    who.user = None  # the page never relies on a console sign-in
    return person, tokens


def _bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _start(client: AsyncClient, tokens: dict[str, Any], **body: Any) -> dict[str, Any]:
    r = await client.post(START, headers=_bearer(tokens), json={"longest_minutes": 60, **body})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    return r.json()


async def _poll(client: AsyncClient, tokens: dict[str, Any], started: dict[str, Any]) -> Any:
    return await client.post(f"{START}/token", headers=_bearer(tokens), json={"step_up_code": started["step_up_code"]})


async def _row(db: AsyncSession) -> BypassStepUp:
    found = (await db.execute(select(BypassStepUp).order_by(BypassStepUp.created_at.desc()).limit(1))).scalar_one()
    await db.refresh(found)
    return found


async def _unthrottle(db: AsyncSession) -> None:
    """Polls a test makes back to back are not too early for the interval."""
    row = await _row(db)
    row.last_polled_at = None
    await db.flush()


def _claims(proof: str, secret_value: str) -> dict[str, Any]:
    key = hmac.new(secret_value.encode(), step_up.PROOF_KEY_LABEL, hashlib.sha256).digest()
    return jwt.decode(proof, key, algorithms=["HS256"], audience=step_up.PROOF_AUDIENCE)


# -- the app -----------------------------------------------------------------------------------


async def test_only_an_apps_live_agent_sign_in_starts_a_step_up(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    console = jwt.encode({"sub": str(person.id), "exp": int(time.time()) + 60}, secret, algorithm="HS256")
    for headers in ({}, {"Authorization": f"Bearer {console}"}, {"Authorization": "Bearer nonsense"}):
        r = await client.post(START, headers=headers, json={})
        assert r.status_code == status.HTTP_401_UNAUTHORIZED, headers
    started = await _start(client, tokens)
    assert started["verification_uri_complete"].endswith(f"/step-up#code={started['user_code']}"), "in the fragment"
    assert (started["expires_in"], started["interval"]) == (600, 5)
    signed_in.user = person
    await client.delete(f"/api/agent-tokens/{tokens['device_id']}")  # the device signed out
    signed_in.user = None
    r = await client.post(START, headers=_bearer(tokens), json={})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED, "a revoked sign-in starts nothing"


async def test_the_password_again_the_decision_and_the_proof_once(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    started = await _start(client, tokens)
    pending = await _poll(client, tokens, started)
    assert (pending.status_code, pending.json()["error"]) == (400, "authorization_pending")

    opened = await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    assert (opened.status_code, opened.json()["state"]) == (200, "sign_in"), opened.text
    assert opened.json()["agent"] is None, "nothing about the step-up before the sign-in"
    early = await client.post(f"{PAGE}/approve")
    assert (early.status_code, early.json()["detail"]["code"]) == (409, "sign_in_first")

    wrong = await client.post(f"{PAGE}/password", json={"email": person.email, "password": "not it"})
    other = await _person(dbsession, "Another right password 8!")
    another = await client.post(f"{PAGE}/password", json={"email": other.email, "password": "Another right password 8!"})
    assert [r.json()["detail"]["code"] for r in (wrong, another)] == ["wrong_credentials"] * 2, "no enumeration"

    right = await client.post(f"{PAGE}/password", json={"email": person.email, "password": PASSWORD})
    assert right.status_code == 200, right.text
    shown = right.json()
    assert (shown["state"], shown["device"], shown["longest_minutes"], shown["method"]) == (
        "decide", "PC-7", 60, "password",
    )
    assert shown["agent"] and shown["signed_in_as"] == person.email
    await _unthrottle(dbsession)
    assert (await _poll(client, tokens, started)).json()["error"] == "authorization_pending", "signed in, not approved"

    approved = await client.post(f"{PAGE}/approve")
    assert (approved.status_code, approved.json()["state"]) == (200, "approved")
    await _unthrottle(dbsession)
    got = await _poll(client, tokens, started)
    assert got.status_code == 200, got.text
    assert got.headers["cache-control"] == "no-store"
    body = got.json()
    claims = _claims(body["proof"], secret)
    assert (claims["typ"], claims["usr"], claims["did"], claims["method"], claims["max_minutes"]) == (
        "bypass_step_up", str(person.id), tokens["device_id"], "password", 60,
    )
    assert claims["agt"] == tokens["agent_id"] and claims["exp"] - claims["auth_time"] == 300
    assert abs(claims["auth_time"] - time.time()) < 10 and 280 <= body["expires_in"] <= 300
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(body["proof"], secret, algorithms=["HS256"], audience=step_up.PROOF_AUDIENCE)
    await _unthrottle(dbsession)
    again = await _poll(client, tokens, started)
    assert again.json()["error"] == "invalid_grant", "once"

    events = (await dbsession.execute(select(AuditEvent).where(AuditEvent.target_type == "bypass_step_up"))).scalars()
    recorded = " ".join(f"{e.action} {e.metadata_json}" for e in events)
    assert "bypass_step_up.approve" in recorded and "bypass_step_up.proof" in recorded
    for secret_value in (body["proof"], started["user_code"], started["step_up_code"]):
        assert secret_value not in recorded, "never a code or the proof in the audit trail"


async def test_a_console_session_counts_for_nothing_and_an_old_sign_in_approves_nothing(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    started = await _start(client, tokens)
    signed_in.user = person  # signed in to the console in this browser
    opened = await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    assert opened.json()["state"] == "sign_in", "the password again, even so"
    assert (await client.post(f"{PAGE}/approve")).status_code == status.HTTP_409_CONFLICT
    await client.post(f"{PAGE}/password", json={"email": person.email, "password": PASSWORD})
    row = await _row(dbsession)
    row.signed_in_at = datetime.now(UTC) - timedelta(minutes=5, seconds=1)
    await dbsession.flush()
    old = await client.post(f"{PAGE}/approve")
    assert (old.status_code, old.json()["detail"]["code"]) == (409, "sign_in_too_old")
    assert (await client.get(PAGE)).json()["state"] == "sign_in", "sign in again"


async def test_only_the_apps_own_sign_in_collects_and_a_denial_or_a_lapse_gives_nothing(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    signed_in.user = person
    other_device = (await _sign_in(client, tokens["agent"], "Laptop")).json()
    signed_in.user = None
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    await client.post(f"{PAGE}/password", json={"email": person.email, "password": PASSWORD})
    await client.post(f"{PAGE}/approve")
    stolen = await _poll(client, other_device, started)
    assert stolen.json()["error"] == "invalid_grant", "another device's sign-in, the same person"

    denied = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": denied["user_code"]})
    assert (await client.post(f"{PAGE}/deny")).json()["state"] == "denied"
    assert (await _poll(client, tokens, denied)).json()["error"] == "access_denied"

    lapsed = await _start(client, tokens)
    row = await _row(dbsession)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await dbsession.flush()
    assert (await _poll(client, tokens, lapsed)).json()["error"] == "expired_token"
    gone = await client.post(f"{PAGE}/begin", json={"code": lapsed["user_code"]})
    assert gone.status_code == status.HTTP_404_NOT_FOUND


async def test_five_wrong_sign_ins_deny_the_step_up(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    codes = [
        (await client.post(f"{PAGE}/password", json={"email": person.email, "password": f"wrong {n}"})).json()["detail"]
        for n in range(5)
    ]
    assert [c["code"] for c in codes] == ["wrong_credentials"] * 4 + ["denied"]
    assert (await _poll(client, tokens, started)).json()["error"] == "access_denied"


# -- a provider forced to authenticate anew -----------------------------------------------------


class _Module:
    """The sign-in module's answers, as a test sets them; what core asked it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, provider: ProviderPublic) -> None:
        self.provider = provider
        self.fresh: list[bool] = []
        self.identity: ExternalIdentity | None = None
        #: The module refuses the answer: why, and its detail.
        self.refusal: tuple[Refusal, str] | None = None
        monkeypatch.setattr(module, "public_providers", self._public)
        monkeypatch.setattr(module, "provider", self._provider)
        monkeypatch.setattr(module, "start", self._start)
        monkeypatch.setattr(module, "finish", self._finish)
        monkeypatch.setattr(module, "password", self._password)

    async def _public(self) -> list[ProviderPublic]:
        return [self.provider]

    async def _provider(self, key: str) -> ProviderPublic | None:
        return self.provider if key == self.provider.key else None

    async def _start(self, key: str, return_url: str, *, fresh: bool = False) -> SignInStart:
        self.fresh.append(fresh)
        return SignInStart(redirect_url="https://idp.example/authorize", transaction="tx-" + "a" * 20)

    async def _finish(self, *_: Any) -> SignInAnswer:
        if self.refusal:
            return SignInAnswer(outcome="refused", refusal=self.refusal[0], detail=self.refusal[1])
        return SignInAnswer(outcome="identity", identity=self.identity)

    async def _password(self, *_: Any) -> SignInAnswer:
        return SignInAnswer(outcome="identity", identity=self.identity)


def _identity(key: str, subject: str, authenticated_at: datetime | None) -> ExternalIdentity:
    return ExternalIdentity(provider=key, provider_name=key, subject=subject, authenticated_at=authenticated_at)


async def _link(db: AsyncSession, user: User, key: str, subject: str) -> None:
    db.add(OAuthAccount(
        id=uuid.uuid4(), user_id=user.id, oauth_name=key, account_id=subject, account_email=user.email,
        access_token="",  # noqa: S106 - none kept
    ))
    await db.flush()


@pytest.fixture
def oidc(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Module]:
    yield _Module(monkeypatch, ProviderPublic(key="entra", name="Entra ID", kind=ProviderKind.REDIRECT))


@pytest.mark.parametrize(("when", "outcome"), [
    (None, "not_fresh"),
    (-180, "not_fresh"),
    (5, None),
], ids=["no-auth-time", "auth-time-before-the-step-up-asked", "fresh"])
async def test_a_provider_must_say_it_authenticated_the_person_after_the_step_up_asked(
    when: int | None, outcome: str | None, oidc: _Module,
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    """OIDC ``prompt=login`` and ``max_age=0``, then ``auth_time`` checked (ASVS 5.0 6.8.4)."""
    person, tokens = await _app(client, dbsession, signed_in)
    await _link(dbsession, person, "entra", "sub-1")
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    went = await client.post(f"{PAGE}/sso/entra")
    assert went.status_code == 200, went.text
    assert oidc.fresh == [True], "the provider is asked to authenticate anew"
    asked = datetime.now(UTC)
    oidc.identity = _identity("entra", "sub-1", None if when is None else asked + timedelta(seconds=when))
    back = await client.get("/api/auth/sso/entra/callback", params={"state": "tx-" + "a" * 20, "code": "c"}, follow_redirects=False)
    assert back.status_code == status.HTTP_302_FOUND, back.text
    if outcome:
        assert back.headers["location"] == f"/step-up?refused={outcome}"
        assert (await client.get(PAGE)).json()["state"] == "sign_in"
        return
    assert back.headers["location"] == "/step-up"
    shown = (await client.get(PAGE)).json()
    assert (shown["state"], shown["method"]) == ("decide", "oidc")
    assert "fapiauth" not in back.headers.get("set-cookie", ""), "nobody is signed in to the console"


async def test_a_provider_sign_in_as_someone_else_or_unlinked_approves_nothing(
    oidc: _Module, signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811
) -> None:
    person, tokens = await _app(client, dbsession, signed_in)
    other = await _user(dbsession)
    await _link(dbsession, other, "entra", "sub-other")
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    await client.post(f"{PAGE}/sso/entra")
    for subject in ("sub-other", "sub-unlinked"):
        oidc.identity = _identity("entra", subject, datetime.now(UTC))
        await client.post(f"{PAGE}/sso/entra")
        back = await client.get("/api/auth/sso/entra/callback", params={"state": "tx-" + "a" * 20, "code": "c"}, follow_redirects=False)
        assert back.headers["location"] == "/step-up?refused=wrong_account", subject
    links = (await dbsession.execute(select(OAuthAccount).where(OAuthAccount.account_id == "sub-unlinked"))).all()
    assert links == [], "a step-up links nothing"


@pytest.mark.parametrize(("refusal", "detail"), [
    (Refusal.NOT_FRESH, "before_asked"),
    (Refusal.PROVIDER_ERROR, "replayed"),
], ids=["not-fresh", "provider-error"])
async def test_a_refusal_by_the_module_is_recorded_as_the_step_ups_as_cores_own_not_fresh(
    refusal: Refusal, detail: str, oidc: _Module,
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811 - the fixtures
) -> None:
    """The row names the step-up, its agent and device, and neither counts as a wrong sign-in (ASVS 5.0 16.3.1)."""
    person, tokens = await _app(client, dbsession, signed_in)
    await _link(dbsession, person, "entra", "sub-1")
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})

    async def answered() -> str:
        await client.post(f"{PAGE}/sso/entra")
        back = await client.get(
            "/api/auth/sso/entra/callback", params={"state": "tx-" + "a" * 20, "code": "c"}, follow_redirects=False,
        )
        return back.headers["location"]

    oidc.identity = _identity("entra", "sub-1", datetime.now(UTC) - timedelta(minutes=10))
    assert await answered() == "/step-up?refused=not_fresh", "core's own check"
    oidc.refusal = (refusal, detail)
    assert await answered() == f"/step-up?refused={refusal.value}", "the module's"

    row = await _row(dbsession)
    named = {"agent": str(row.agent_id), "device": str(row.device_id), "provider": "entra"}
    rows = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "bypass_step_up.sign_in").order_by(AuditEvent.time),
    )).scalars().all()
    assert [(e.target_type, e.target_id, e.result, json.loads(e.metadata_json or "{}")) for e in rows] == [
        ("bypass_step_up", str(row.id), "deny", {**named, "reason": "not_fresh"}),
        ("bypass_step_up", str(row.id), "deny", {**named, "reason": refusal.value}),
    ]
    assert row.failed_sign_ins == 0, "neither is a wrong sign-in"
    assert (await dbsession.execute(select(BypassStepUpFailure))).all() == []
    module_rows = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "auth.sign_in.refused"),
    )).scalars().all()
    assert [json.loads(e.metadata_json or "{}") for e in module_rows] == [
        {"provider": "entra", "reason": refusal.value, "detail": detail},
    ], "the module's own record stays"
    assert (await client.get(PAGE)).json()["state"] == "sign_in", "the person may sign in again"


async def test_saml_and_the_directory_are_recorded_as_such(
    monkeypatch: pytest.MonkeyPatch, signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
    secret: str,  # noqa: F811 - the fixture
) -> None:
    saml = _Module(monkeypatch, ProviderPublic(key="adfs", name="AD FS", kind=ProviderKind.REDIRECT, posts_back=True))
    person, tokens = await _app(client, dbsession, signed_in)
    await _link(dbsession, person, "adfs", "nameid-1")
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    await client.post(f"{PAGE}/sso/adfs")
    saml.identity = _identity("adfs", "nameid-1", datetime.now(UTC) - timedelta(minutes=10))
    old = await client.post("/api/auth/sso/adfs/acs", data={"RelayState": "tx-" + "a" * 20, "SAMLResponse": "x"}, follow_redirects=False)
    assert old.headers["location"] == "/step-up?refused=not_fresh", "an old AuthnInstant"
    saml.identity = _identity("adfs", "nameid-1", datetime.now(UTC))
    await client.post(f"{PAGE}/sso/adfs")
    ok = await client.post("/api/auth/sso/adfs/acs", data={"RelayState": "tx-" + "a" * 20, "SAMLResponse": "x"}, follow_redirects=False)
    assert ok.headers["location"] == "/step-up"
    assert (await client.get(PAGE)).json()["method"] == "saml"

    ldap = _Module(monkeypatch, ProviderPublic(key="corp-ad", name="Corp AD", kind=ProviderKind.PASSWORD))
    await _link(dbsession, person, "corp-ad", "cn=person")
    started = await _start(client, tokens)
    await client.post(f"{PAGE}/begin", json={"code": started["user_code"]})
    ldap.identity = _identity("corp-ad", "cn=person", None)
    shown = await client.post(f"{PAGE}/directory/corp-ad", json={"username": "person", "password": "pw"})
    assert (shown.status_code, shown.json()["method"]) == (200, "ldap"), shown.text


# -- the settings ------------------------------------------------------------------------------


async def _put(client: AsyncClient, key: str, value: Any) -> Any:
    return await client.put(f"/api/admin/system/settings/values/{key}", json={"value": value})


async def test_bypass_is_turned_off_without_root_mode_and_on_only_with_it(
    fastapi_app: FastAPI, signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    """Owner, 2026-11-09: only the narrowing direction gets easier; both are recorded."""
    signed_in.user = await _user(dbsession, admin=True)
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    on = await _put(client, "approvals.bypass_allowed", True)
    assert on.status_code == status.HTTP_403_FORBIDDEN, "turning it on needs Root Mode"
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    assert (await _put(client, "approvals.bypass_allowed", True)).status_code == status.HTTP_200_OK
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    off = await _put(client, "approvals.bypass_allowed", False)
    assert off.status_code == status.HTTP_200_OK, off.text
    events = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.target_id == "approvals.bypass_allowed").order_by(AuditEvent.time),
    )).scalars().all()
    recorded = [json.loads(e.metadata_json or "{}") for e in events]
    assert [(r.get("from"), r.get("to"), r.get("root_mode")) for r in recorded] == [(False, True, True), (True, False, False)]
    assert all(e.severity == "high" for e in events)
    fastapi_app.dependency_overrides.pop(get_root_mode_active, None)


@pytest.mark.parametrize(("value", "code"), [(0, 200), (2000, 200), (2001, 422), (-1, 422)])
async def test_the_result_summary_length_is_a_setting_from_0_to_2000(
    value: int, code: int, fastapi_app: FastAPI, signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    r = await _put(client, "approvals.result_summary_chars", value)
    assert r.status_code == code, r.text
    fastapi_app.dependency_overrides.pop(get_root_mode_active, None)
