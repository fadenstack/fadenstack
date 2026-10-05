"""A chat's PII mode: what the Privacy panel shows is what the chat's requests get.

The panel computed the effective policy with the admin's weaken right, the
requests without it: the panel said tokenize, the turn was redacted ("Hello
<PERSON>", 5 October on .61). Both now use ``resolve_session_pii``. A chat's
own mode is applied when the tenant allows it (``allow_mode_override``), or
the agent it runs under does (its setting overrides the tenant's), or the
override was saved by someone who may weaken -- which the gateway stores with
the override, from the backend's signed grant only.
"""

from __future__ import annotations

import importlib.util
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from faden_gateway.db.models.gateway import ProviderType, SessionPIIOverrideRow, TenantLLMPolicy
from faden_gateway.services.gateway.auth import AuthContext, pii_weaken_granted, verify_token
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.pii_policy import (
    SessionPIIOverride,
    mode_switch_allowed,
    resolve_session_pii,
)
from faden_gateway.services.gateway.service import _resolve_pii_policy
from faden_gateway.services.registry import service_registry
from faden_gateway.settings import settings
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    TEST_JWT_SECRET,
    _install_pii,
    _model_answers,
    _Observability,
    _pii,
    _seed,
    _session,
    _token,
)

MIGRATION = Path(__file__).parents[1] / "faden_gateway/db/migrations/versions/2026-10-05-12-00_p11w34k3n.py"
REDACT_FLOOR = {
    "egress": {"enabled_for_cloud": True, "mode": "redact", "fail_action": "block"},
    "presidio": {"threshold": 0.5, "entities": ["PERSON"]},
}


class _Tenant:
    def __init__(self, *, allow_mode_override: bool, pii_config: dict[str, Any] | None = None) -> None:
        self.pii_config = pii_config if pii_config is not None else REDACT_FLOOR
        self.allow_mode_override = allow_mode_override


class _Row:
    """A ``SessionPIIOverrideRow`` as the DAO returns it."""

    def __init__(self, *, allow_weaken: bool, **fields: Any) -> None:
        for name in (
            "pii_enabled", "egress_enabled_for_cloud", "egress_enabled_for_local", "egress_mode",
            "egress_fail_action", "telemetry_enabled", "telemetry_mode", "presidio_threshold",
            "presidio_entities_add",
        ):
            setattr(self, name, fields.get(name))
        self.allow_weaken = allow_weaken


def _grant(sub: str = "user-1", sid: str = "", *, secret: str = TEST_JWT_SECRET, **claims: Any) -> str:
    now = int(time.time())
    body = {"typ": "pii_weaken", "sub": sub, "sid": sid, "iat": now, "exp": now + 60, **claims}
    return jwt.encode(body, secret, algorithm="HS256")


# -- who may switch the mode -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tenant", "agent", "weaken", "allowed"),
    [
        (False, None, False, False),
        (True, None, False, True),
        (False, True, False, True),
        (True, True, False, True),
        (True, False, False, False),
        (False, False, False, False),
        (False, None, True, True),
        (False, False, True, True),
        (True, False, True, True),
    ],
)
def test_the_agent_decides_over_the_tenant_and_weaken_over_both(
    tenant: bool, agent: bool | None, weaken: bool, allowed: bool,
) -> None:
    assert mode_switch_allowed(tenant_default=tenant, agent_setting=agent, weaken=weaken) is allowed


@pytest.mark.parametrize(("tenant", "weaken", "mode"), [
    (False, False, "redact"),
    (True, False, "tokenize_reversible"),
    (False, True, "tokenize_reversible"),
])
def test_a_chats_mode_is_applied_only_when_allowed(tenant: bool, weaken: bool, mode: str) -> None:
    resolved = resolve_session_pii(
        _Tenant(allow_mode_override=tenant),
        _Row(allow_weaken=weaken, egress_mode="tokenize_reversible"),
        default_policy=None,
    )
    assert resolved.effective is not None
    assert resolved.effective.egress.mode == mode
    assert resolved.requested_mode == "tokenize_reversible"
    assert resolved.mode_applied is (mode == "tokenize_reversible")
    assert resolved.mode_switch_allowed is (tenant or weaken)


def test_an_override_without_the_column_counts_as_saved_without_the_right() -> None:
    """A row (or a stand-in) with no ``allow_weaken`` never weakens; nor does a truthy non-boolean."""

    class _Old:
        pii_enabled = egress_enabled_for_cloud = egress_enabled_for_local = None
        egress_fail_action = telemetry_enabled = telemetry_mode = None
        presidio_threshold = presidio_entities_add = None
        egress_mode = "tokenize_reversible"

    for row in (_Old(), _Row(allow_weaken="yes", egress_mode="tokenize_reversible")):  # type: ignore[arg-type]
        resolved = resolve_session_pii(_Tenant(allow_mode_override=False), row, default_policy=None)
        assert resolved.weaken is False
        assert resolved.effective is not None and resolved.effective.egress.mode == "redact"


def test_the_weaken_right_also_lets_an_admin_turn_protection_off() -> None:
    row = _Row(allow_weaken=True, egress_fail_action="allow", egress_enabled_for_cloud=False)
    resolved = resolve_session_pii(_Tenant(allow_mode_override=False), row, default_policy=None)
    assert resolved.effective is not None
    assert resolved.effective.egress.fail_action == "allow"
    assert resolved.effective.egress.enabled_for_cloud is False
    without = resolve_session_pii(
        _Tenant(allow_mode_override=True),
        _Row(allow_weaken=False, egress_fail_action="allow", egress_enabled_for_cloud=False),
        default_policy=None,
    )
    assert without.effective is not None
    assert without.effective.egress.fail_action == "block", "the tenant's mode switch is not a weaken right"
    assert without.effective.egress.enabled_for_cloud is True


def test_an_unknown_mode_is_no_request_and_no_policy_is_none() -> None:
    resolved = resolve_session_pii(
        _Tenant(allow_mode_override=True), _Row(allow_weaken=False, egress_mode="plaintext"), default_policy=None,
    )
    assert resolved.requested_mode is None and resolved.mode_applied
    assert resolved.effective is not None and resolved.effective.egress.mode == "redact"

    nothing = resolve_session_pii(
        _Tenant(allow_mode_override=True, pii_config={}), _Row(allow_weaken=True, egress_mode="redact"),
        default_policy=None,
    )
    assert nothing.floor is None and nothing.effective is None


def test_the_system_default_is_the_floor_when_the_tenant_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pii_default_policy", REDACT_FLOOR, raising=False)
    tenant = _Tenant(allow_mode_override=True, pii_config={})
    resolved = resolve_session_pii(tenant, SessionPIIOverride(egress_mode="tokenize_reversible"))
    assert resolved.floor is not None and resolved.floor.egress.mode == "redact"
    assert resolved.effective is not None and resolved.effective.egress.mode == "tokenize_reversible"


@pytest.mark.parametrize("tenant", [False, True])
@pytest.mark.parametrize("agent", [None, True, False])
def test_the_request_path_resolves_through_the_same_helper(tenant: bool, agent: bool | None) -> None:
    """``_resolve_pii_policy`` (each request) and ``resolve_session_pii`` (the panel) agree."""
    override = SessionPIIOverride(egress_mode="tokenize_reversible")
    policy = _Tenant(allow_mode_override=tenant)
    request = _resolve_pii_policy(policy, session_override=override, agent_allow_mode_override=agent)
    panel = resolve_session_pii(policy, override, agent_allow_mode_override=agent).effective
    assert request == panel
    expected = agent if agent is not None else tenant
    assert request is not None
    assert request.egress.mode == ("tokenize_reversible" if expected else "redact")


def test_without_an_override_a_request_gets_the_floor() -> None:
    policy = _Tenant(allow_mode_override=True)
    floor = resolve_session_pii(policy).floor
    assert floor is not None and floor.egress.mode == "redact"
    assert _resolve_pii_policy(policy) == floor
    assert _resolve_pii_policy(policy, agent_allow_mode_override=True) == floor


# -- the weaken grant: only the backend's ------------------------------------------------------


def test_only_the_backends_grant_for_this_user_and_chat_is_a_weaken_right() -> None:
    auth = AuthContext(user_id="user-1", tenant_id="tenant-a", raw_claims={})
    sid = str(uuid.uuid4())
    assert pii_weaken_granted(_grant(sid=sid), auth, sid) is True
    assert pii_weaken_granted(_grant(), auth, "") is True, "before a chat exists"

    refused = {
        "nothing": None,
        "the old header value": "true",
        "the caller's own token": _token(),
        "another user's grant": _grant(sub="user-2", sid=sid),
        "another chat's grant": _grant(sid=str(uuid.uuid4())),
        "an expired grant": _grant(sid=sid, exp=int(time.time()) - 5),
        "another type": _grant(sid=sid, typ="agent"),
        "another secret": _grant(sid=sid, secret="someone-elses-secret-32-bytes-long!"),  # noqa: S106
        "no expiry": jwt.encode({"typ": "pii_weaken", "sub": "user-1", "sid": sid}, TEST_JWT_SECRET, "HS256"),
    }
    for why, value in refused.items():
        assert pii_weaken_granted(value, auth, sid) is False, why


def test_a_weaken_grant_is_never_a_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured audience, nothing else would tell a grant from a user's token."""
    monkeypatch.setattr(settings, "jwt_audience", None)
    with pytest.raises(GatewayError) as caught:
        verify_token(_grant(sid=str(uuid.uuid4())))
    assert caught.value.status_code == 401
    assert verify_token(_token())["sub"]


# -- the panel's endpoints ----------------------------------------------------------------------


async def _tenant(db: AsyncSession, *, allow_mode_override: bool) -> None:
    db.add(TenantLLMPolicy(
        tenant_id="tenant-a", allowed_model_aliases=[ALIAS], allowed_provider_types=["vllm"],
        pii_config=REDACT_FLOOR, allow_mode_override=allow_mode_override, rpm_limit=100, tpm_limit=100_000,
    ))
    await db.flush()


async def _stored(db: AsyncSession, sid: uuid.UUID) -> bool:
    row = (await db.execute(select(SessionPIIOverrideRow).where(SessionPIIOverrideRow.session_id == sid))).scalar_one()
    await db.refresh(row)
    return row.allow_weaken


def _auth(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", **extra}


@pytest.mark.anyio
async def test_a_non_admin_cannot_store_the_weaken_right(client: AsyncClient, db_session: AsyncSession) -> None:
    await _tenant(db_session, allow_mode_override=False)
    sid = await _session(db_session)
    url = f"/v1/sessions/{sid}/pii-policy"
    tokenize = {"egress_mode": "tokenize_reversible"}

    attempts = [
        ({"X-PII-Allow-Weaken": "true"}, tokenize),
        ({}, {**tokenize, "allow_weaken": True}),
        ({"X-PII-Allow-Weaken": _token()}, tokenize),
        ({"X-PII-Allow-Weaken": _grant(sub="user-2", sid=str(sid))}, tokenize),
        ({"X-PII-Allow-Weaken": _grant(sid=str(uuid.uuid4()))}, tokenize),
    ]
    for headers, body in attempts:
        r = await client.patch(url, headers=_auth(**headers), json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert await _stored(db_session, sid) is False, headers or body
        assert data["effective"]["egress"]["mode"] == "redact", "not shown as applied"
        assert data["mode_applied"] is False
        assert data["mode_switch_allowed"] is False
        assert data["can_switch_mode"] is False


@pytest.mark.anyio
async def test_an_admins_override_keeps_its_right_until_saved_without_it(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _tenant(db_session, allow_mode_override=False)
    sid = await _session(db_session)
    url = f"/v1/sessions/{sid}/pii-policy"

    r = await client.patch(url, headers=_auth(**{"X-PII-Allow-Weaken": _grant(sid=str(sid))}),
                           json={"egress_mode": "tokenize_reversible"})
    assert r.status_code == 200, r.text
    assert await _stored(db_session, sid) is True
    assert r.json()["effective"]["egress"]["mode"] == "tokenize_reversible"
    assert r.json()["mode_applied"] is True

    # A read without the grant shows what the requests get: still tokenize.
    data = (await client.get(url, headers=_auth())).json()
    assert data["effective"]["egress"]["mode"] == "tokenize_reversible"
    assert (data["mode_switch_allowed"], data["can_switch_mode"], data["mode_applied"]) == (True, True, True)

    # Saved again by someone without the right (the same user, no longer an admin): the right goes.
    r = await client.patch(url, headers=_auth(), json={"presidio_threshold": 0.7})
    assert await _stored(db_session, sid) is False
    data = r.json()
    assert data["effective"]["egress"]["mode"] == "redact"
    assert (data["mode_switch_allowed"], data["can_switch_mode"], data["mode_applied"]) == (False, False, False)

    # An admin reading it may switch it again, though it is not applied yet.
    data = (await client.get(url, headers=_auth(**{"X-PII-Allow-Weaken": _grant(sid=str(sid))}))).json()
    assert (data["mode_switch_allowed"], data["can_switch_mode"], data["mode_applied"]) == (False, True, False)


@pytest.mark.anyio
async def test_the_tenant_switch_lets_every_chat_choose(client: AsyncClient, db_session: AsyncSession) -> None:
    await _tenant(db_session, allow_mode_override=True)
    sid = await _session(db_session)
    r = await client.patch(f"/v1/sessions/{sid}/pii-policy", headers=_auth(), json={"egress_mode": "tokenize_reversible"})
    data = r.json()
    assert await _stored(db_session, sid) is False
    assert data["effective"]["egress"]["mode"] == "tokenize_reversible"
    assert (data["mode_switch_allowed"], data["can_switch_mode"], data["mode_applied"]) == (True, True, True)


@pytest.mark.anyio
async def test_the_defaults_say_whether_a_new_chat_may_choose(client: AsyncClient, db_session: AsyncSession) -> None:
    await _tenant(db_session, allow_mode_override=False)
    data = (await client.get("/v1/pii-defaults", headers=_auth())).json()
    assert (data["mode_switch_allowed"], data["can_switch_mode"]) == (False, False)
    data = (await client.get("/v1/pii-defaults", headers=_auth(**{"X-PII-Allow-Weaken": _grant()}))).json()
    assert (data["mode_switch_allowed"], data["can_switch_mode"]) == (False, True)
    assert data["effective"]["egress"]["mode"] == "redact"


# -- what a request gets ------------------------------------------------------------------------


@pytest.fixture
def pii_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The PII service, faked: it records the mode of each scan. Switched off again after the test."""
    yield _install_pii(monkeypatch)
    service_registry.configure("pii", enabled=False, url=None)


@pytest.mark.anyio
@pytest.mark.parametrize("tenant", [False, True], ids=["tenant-off", "tenant-on"])
@pytest.mark.parametrize("weaken", [False, True], ids=["no-weaken", "weaken-stored"])
async def test_a_chats_request_gets_the_mode_its_panel_shows(
    tenant: bool, weaken: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    pii_service: Any,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("redact"))
    policy = await db_session.get(TenantLLMPolicy, "tenant-a")
    assert policy is not None
    policy.allow_mode_override = tenant
    await db_session.commit()
    fastapi_app.state.gateway_observability = _Observability()
    fake = pii_service
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "Hello", sent)
    sid = await _session(db_session)

    grant = {"X-PII-Allow-Weaken": _grant(sid=str(sid))} if weaken else {}
    panel = (await client.patch(
        f"/v1/sessions/{sid}/pii-policy", headers=_auth(**grant), json={"egress_mode": "tokenize_reversible"},
    )).json()

    r = await client.post("/v1/chat/completions", headers=_auth(), json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "I am Alice"}],
    })
    assert r.status_code == 200, r.text
    applied = tenant or weaken
    assert fake.modes == ["tokenize" if applied else "redact"]
    assert panel["effective"]["egress"]["mode"] == ("tokenize_reversible" if applied else "redact")
    assert panel["mode_applied"] is applied
    assert ("[PERSON_1]" if applied else "<PERSON>") in str(sent[0]["messages"])


# -- the migration ------------------------------------------------------------------------------


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("p11w34k3n", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_follows_the_last_one_and_leaves_one_head() -> None:
    module = _migration()
    assert (module.revision, module.down_revision) == ("p11w34k3n", "m4n1f35t")
    scripts = ScriptDirectory.from_config(Config(str(Path(__file__).parents[1] / "alembic.ini")))
    assert len(scripts.get_heads()) == 1
    assert scripts.get_revision("p11w34k3n").down_revision == "m4n1f35t"


DSN = os.environ.get("FADEN_TEST_GATEWAY_DSN", "")


@pytest.mark.anyio
@pytest.mark.skipif(not DSN, reason="FADEN_TEST_GATEWAY_DSN names no Postgres (postgresql+asyncpg://...)")
async def test_the_migration_on_postgres_reads_old_overrides_as_without_the_right() -> None:
    """In a schema of its own, in a transaction that is rolled back: nothing stays behind."""
    module = _migration()
    schema = f"pii_weaken_{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(DSN)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.execute(text(
                    "CREATE TABLE session_pii_override (session_id uuid PRIMARY KEY, egress_mode varchar(32))",
                ))
                await conn.execute(text(
                    "INSERT INTO session_pii_override VALUES (gen_random_uuid(), 'tokenize_reversible')",
                ))

                def run(sync_conn: Any, step: str) -> None:
                    with Operations.context(MigrationContext.configure(sync_conn)):
                        getattr(module, step)()

                await conn.run_sync(run, "upgrade")
                old = (await conn.execute(text("SELECT allow_weaken FROM session_pii_override"))).scalar_one()
                assert old is False
                await conn.execute(text("INSERT INTO session_pii_override (session_id) VALUES (gen_random_uuid())"))
                nullable = (await conn.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'session_pii_override' AND column_name = 'allow_weaken'",
                ), {"s": schema})).scalar_one()
                assert nullable == "NO"

                await conn.run_sync(run, "downgrade")
                gone = (await conn.execute(text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = :s AND column_name = 'allow_weaken'",
                ), {"s": schema})).scalar_one()
                assert gone == 0
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
