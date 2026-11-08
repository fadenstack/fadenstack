"""Grant limits from extensions, the results covered calls leave behind, and a grant's evidence for auditors.

sdk/40 A4.0. Without a database: how the limits combine, that one which fails makes no grant, the tenant's mark,
how a recorded place is matched, how a client's result reads. On Postgres (``FADEN_TEST_GATEWAY_DSN``): the limits
cut a grant at issue, at extend and in the profile and set what it records; a failing limit makes none; a covered
call's result is kept from the gateway's run and from the device's next request; the evidence route decrypts only
for a grant the backend signed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from faden_gateway.db import crypto
from faden_gateway.db.models.gateway import (
    ApprovalGrant,
    ApprovalGrantCall,
    ApprovalGrantChange,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    PrivacyMode,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
)
from faden_gateway.services import extensions
from faden_gateway.services.extensions import ExtensionRegistry, GrantContext, GrantLimit
from faden_gateway.services.gateway import approval_settings, grants, tenant_restrictions
from faden_gateway.settings import settings
from tests.test_agent_routes import (  # noqa: F401 - backend: the fixture
    _agent_token,
    _Backend,
    _profile,
    backend,
)
from tests.test_bypass import SUPPORTS, _bypass
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _call, _Model, _say
from tests.test_gateway_pipeline_combined import ALIAS, _Observability
from tests.test_grants import (  # noqa: F401 - db_session, engine: the fixtures
    GRANTS,
    OWNER,
    TIME,
    UPDATE_PAGE,
    _auth,
    _chat,
    _seed,
    _told,
    db_session,
    engine,
    needs_postgres,
)
from tests.test_governance import EVENTS

CTX = GrantContext(tenant_id="tenant-a", user_id="u-1", agent_id="a-1", groups=("team-1",), kind="time")


def _answering(longest: Any = None, level: Any = None) -> Any:
    async def rule(_: GrantContext) -> GrantLimit:
        return GrantLimit(longest_minutes=longest, evidence_level=level)

    return rule


async def _silent(_: GrantContext) -> None:
    return None


async def _broken(_: GrantContext) -> None:
    msg = "the limits' database is away"
    raise RuntimeError(msg)


# -- how the limits combine, without a database ---------------------------------------------------


@pytest.mark.anyio
async def test_the_shortest_longest_grant_and_the_most_recorded_level_apply() -> None:
    rules = [_answering(60, "calls"), _answering(30), _answering(None, "results"), _silent]
    assert await grants.extension_limits(rules, CTX) == grants.Limits(longest_minutes=30, evidence_level="results")
    assert await grants.extension_limits([_answering(90, "decisions")], CTX) == grants.Limits(90, "decisions")
    assert await grants.extension_limits([], CTX) == grants.Limits(), "no limits: no word"
    assert await grants.extension_limits([_silent], CTX) == grants.Limits()


@pytest.mark.anyio
@pytest.mark.parametrize("rule", [
    _broken, _answering(0), _answering(-5), _answering(True), _answering("60"), _answering(None, "everything"),
], ids=["fails", "zero-minutes", "negative", "a-bool", "a-string", "unknown-level"])
async def test_a_limit_that_fails_or_answers_no_limit_makes_no_grant(rule: Any) -> None:
    """ASVS 5.0 V8.3.1: a limit that cannot be applied must not widen a grant."""
    with pytest.raises(grants.LimitsUnavailableError):
        await grants.extension_limits([_answering(60), rule], CTX)


def test_the_limits_narrow_the_installations_longest_and_set_the_level_or_cores_default() -> None:
    assert grants.Limits(longest_minutes=45).longest(120) == 45
    assert grants.Limits(longest_minutes=200).longest(120) == 120, "never longer than the installation's"
    assert grants.Limits().longest(120) == 120
    assert [grants.Limits().evidence(kind) for kind in ("time", "work", "bypass")] == ["decisions", "calls", "calls"]
    assert grants.Limits(evidence_level="decisions").evidence("bypass") == "decisions", "an admin's level (L-60)"
    assert grants.Limits(evidence_level="results").evidence("time") == "results"


@pytest.mark.anyio
async def test_the_tenants_mark_makes_no_grant_while_nothing_installed_enforces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ExtensionRegistry()
    marks = tenant_restrictions.TenantRestrictions("postgresql://unused", registry)
    names: set[str] = {tenant_restrictions.GRANT_LIMITS}

    async def read(_: str) -> frozenset[str]:
        return frozenset(names)

    monkeypatch.setattr(marks, "names", read)
    with pytest.raises(RuntimeError):
        await marks.grant_limit(CTX)
    with pytest.raises(grants.LimitsUnavailableError):
        await grants.extension_limits([marks.grant_limit], CTX)
    registry.tenant_restrictions.add(tenant_restrictions.GRANT_LIMITS)
    assert await marks.grant_limit(CTX) is None, "enforced by an installed extension"
    registry.tenant_restrictions.clear()
    names.clear()
    assert await marks.grant_limit(CTX) is None, "nothing kept, nothing held back"


@pytest.mark.parametrize(("place", "value", "inside"), [
    ("C:\\Docs\\Plan.docx", "c:/docs/plan.docx", True),
    ("C:\\Docs", "C:\\Docs\\Sub\\Plan.docx", True),
    ("C:\\Docs", "C:\\Docs2\\Plan.docx", False),
    ("C:\\Docs", "C:\\Docs\\..\\Secret\\x.docx", False),
    ("https://intranet.example.com", "https://intranet.example.com/wiki/page", True),
    ("https://intranet.example.com", "https://intranet.example.com.evil.net/", False),
    ("C:\\Docs", None, False),
], ids=["same-document", "under-a-folder", "sibling-folder", "climbs-out", "page-of-a-site", "lookalike-site",
        "no-place"])
def test_a_recorded_place_is_found_by_the_place_it_lies_in(place: str, value: str | None, inside: bool) -> None:
    assert grants.place_matches(place, value) is inside


def test_a_client_result_reads_as_failed_when_it_starts_as_the_sdks_word_a_failure() -> None:
    assert grants.client_outcome("Error: the page is locked.") == "error"
    assert grants.client_outcome("  error while saving") == "error"
    assert grants.client_outcome("Saved page 3. No errors.") == "ok"
    assert len(grants.summary("x" * 2000)) == grants.RESULT_CHARS == 500
    assert grants.summary(None) == ""


# -- the limits on the grant API, end to end on Postgres -------------------------------------------


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """The registered grant limits, empty unless a test adds some; the settings Bypass allowed."""
    rules: list[Any] = []
    monkeypatch.setattr(extensions.registry, "grant_limits", rules)
    approval_settings.remember(approval_settings.ApprovalSettings(bypass_allowed=True), ttl_sec=3600)
    yield rules
    approval_settings.forget()


async def _profile_of(client: AsyncClient, **headers: str) -> dict[str, Any]:
    r = await client.get(
        "/v1/agents/eplan-assistant/profile", headers={"Authorization": f"Bearer {_agent_token()}", **headers},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
@needs_postgres
async def test_a_limit_cuts_the_grant_at_issue_at_extend_and_in_the_profile_and_sets_what_it_records(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, limits: list[Any],  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    seen: list[GrantContext] = []

    async def rule(ctx: GrantContext) -> GrantLimit:
        seen.append(ctx)
        return GrantLimit(longest_minutes=45, evidence_level="results")

    limits.append(rule)
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    profile = await _profile_of(client, **SUPPORTS)
    assert profile["approvals"]["longest_grant_minutes"] == 45
    assert profile["approvals"]["bypass"] == {"allowed": True, "longest_minutes": 45, "evidence": "results"}
    r = await client.post(GRANTS, headers=_auth(), json={"kind": "time", "tools": ["update_page"], "minutes": 90})
    assert r.status_code == 201, r.text
    made = r.json()
    assert (made["narrowed"], made["evidence_level"]) == (["minutes"], "results")
    assert 2690 <= made["remaining_s"] <= 2700, "the extension's 45 minutes, not the installation's 120"
    row = (await db_session.execute(select(ApprovalGrant).where(ApprovalGrant.id == uuid.UUID(made["id"])))).scalar_one()
    assert (row.evidence_level, row.policy_version) == ("results", "v3/45m")
    assert seen[-1].kind == "time" and seen[-1].agent_id == _profile().id
    longer = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(), json={"minutes": 30})
    assert (longer.status_code, longer.json()["error"]["code"]) == (422, "grant_at_longest"), "45 from its start"
    assert (await db_session.execute(select(ApprovalGrantChange))).scalars().all() == [], "nothing extended"


@pytest.mark.anyio
@needs_postgres
async def test_a_limit_that_fails_makes_and_extends_no_grant_and_the_profile_offers_none(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, limits: list[Any],  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    made = await client.post(GRANTS, headers=_auth(), json={"kind": "time", "tools": ["update_page"], "minutes": 30})
    assert made.status_code == 201
    limits.append(_broken)
    for body in ({"kind": "time", "tools": ["update_page"], "minutes": 30}, {"kind": "bypass", "minutes": 30}):
        r = await client.post(GRANTS, headers={**_auth(), **SUPPORTS}, json=body)
        assert (r.status_code, r.json()["error"]["code"]) == (503, "grant_limits_unavailable"), r.text
    extended = await client.post(f"{GRANTS}/{made.json()['id']}/extend", headers=_auth(), json={"minutes": 5})
    assert (extended.status_code, extended.json()["error"]["code"]) == (503, "grant_limits_unavailable")
    count = (await db_session.execute(text("SELECT count(*) FROM approval_grant"))).scalar_one()
    assert count == 1, "only the grant made before"
    profile = await _profile_of(client, **SUPPORTS)
    assert profile["approvals"]["longest_grant_minutes"] == 0, "no duration to offer"
    assert profile["approvals"]["bypass"]["allowed"] is False
    assert profile["tool_modes"]["max"] == "auto"


# -- what covered calls leave behind --------------------------------------------------------------


@pytest.fixture
def sealed() -> Iterator[None]:
    """Column encryption on, as in production (the tests run without a master key otherwise)."""
    crypto.configure("test-master-key-for-grant-evidence")
    yield
    crypto._key_provider = None  # noqa: SLF001 - back to the tests' plaintext
    crypto._previous_provider = None  # noqa: SLF001


@pytest.mark.anyio
@needs_postgres
async def test_a_results_grant_keeps_the_arguments_and_a_noted_result_encrypted(
    engine: AsyncEngine, sealed: None,  # noqa: F811 - the fixture
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        grant = await grants.issue(session, OWNER, TIME, evidence_level="results")
    call = grants.Call(request_id="r1", call_id="c1", tool_id="update_page", tool_class="write",
                       arguments={"page": 3, "text": "Anna's notes"})
    covered = await grants.cover_call(engine, OWNER, call)
    assert covered is not None and (covered.grant_id, covered.evidence_level) == (grant.id, "results")
    assert await grants.note_result(engine, covered.record_id, error=False, text="Saved. " + "x" * 900)
    async with AsyncSession(engine) as session:
        row = (await session.execute(select(ApprovalGrantCall))).scalar_one()
        raw = (await session.execute(text("SELECT arguments, result FROM approval_grant_call"))).one()
    assert (row.arguments, row.outcome, len(row.result), row.result_at is not None) == (
        {"page": 3, "text": "Anna's notes"}, "ok", 500, True,
    )
    assert "Saved." not in raw.result and "Anna" not in raw.arguments, "encrypted at rest"
    async with AsyncSession(engine, expire_on_commit=False) as session:
        plain = await grants.issue(session, replace(OWNER, device_id="device-9"), TIME)
    other = await grants.cover_call(engine, replace(OWNER, device_id="device-9"), replace(call, call_id="c2"))
    assert other is not None and (other.grant_id, other.evidence_level) == (plain.id, "decisions")


@pytest.mark.anyio
@needs_postgres
async def test_the_devices_next_request_gives_the_gateway_its_own_sight_of_a_covered_calls_result(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixture
    limits: list[Any], backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """L-61's start: a covered client call's result is seen when it comes back; L-60: kept at ``results``."""
    await _seed(db_session)
    backend.put(_profile(host_tool_contract=({"name": "update_page", "risk": "write"},)))
    fastapi_app.state.agent_directory.invalidate()
    limits.append(_answering(None, "results"))
    grant = (await client.post(GRANTS, headers=_auth(), json={
        "kind": "time", "tools": ["update_page"], "minutes": 30,
    })).json()
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    assert _told(await _chat(client)) == [("used", None)]
    failed = "Error: page 3 is locked by Anna. " + "y" * 700

    async def follow_up(**claims: Any) -> None:
        _Model(monkeypatch, _say("It is locked."))
        r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
            "model": "x", "stream": True, "tools": [UPDATE_PAGE], "messages": [
                {"role": "user", "content": "Fix it."},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "update_page", "arguments": '{"page": 3}'},
                }]},
                {"role": "tool", "tool_call_id": "call_1", "content": failed},
            ],
        }, headers={**_auth(**claims), **EVENTS})
        assert r.status_code == 200, r.text

    await follow_up(did="device-8")
    row = (await db_session.execute(select(ApprovalGrantCall))).scalar_one()
    assert row.result_at is None, "another device's request says nothing of this one's calls"
    await follow_up()
    db_session.expire_all()
    row = (await db_session.execute(select(ApprovalGrantCall))).scalar_one()
    assert (str(row.grant_id), row.outcome, row.result, row.result_at is not None) == (
        grant["id"], "error", failed[:500], True,
    )
    noted_at = row.result_at
    await follow_up()
    db_session.expire_all()
    assert (await db_session.execute(select(ApprovalGrantCall.result_at))).scalar_one() == noted_at, "noted once"


@pytest.mark.anyio
@needs_postgres
async def test_at_the_calls_level_the_gateway_notes_seeing_the_result_and_keeps_none_of_it(
    engine: AsyncEngine,  # noqa: F811 - the fixture
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await grants.issue(session, OWNER, TIME, evidence_level="calls")
    call = grants.Call(request_id="r1", call_id="c1", tool_id="update_page", tool_class="write", side="client")
    assert await grants.cover_call(engine, OWNER, call) is not None
    gateway_side = replace(call, call_id="c2", side="gateway")
    assert await grants.cover_call(engine, OWNER, gateway_side) is not None
    noted = await grants.note_client_results(engine, OWNER, {"c1": "Saved.", "c2": "Saved.", "c9": "?"})
    assert noted == 1, "only the client's own call, and only one it ran"
    async with AsyncSession(engine) as session:
        rows = {r.call_id: r for r in (await session.execute(select(ApprovalGrantCall))).scalars()}
    assert (rows["c1"].result_at is not None, rows["c1"].outcome, rows["c1"].result) == (True, None, None)
    assert rows["c2"].result_at is None
    elsewhere = await grants.note_client_results(engine, replace(OWNER, user_id="someone-else"), {"c1": "x"})
    assert elsewhere == 0


async def _seed_tool_model(fastapi_app: FastAPI, db: AsyncSession) -> None:
    """A model that calls the gateway's own tools, for tenant-a -- in the order Postgres's keys need."""
    now = datetime.now(UTC)
    instance_id = uuid.uuid4()
    await db.merge(LLMModelAlias(alias=ALIAS, description="", enabled=True, created_at=now, updated_at=now))
    db.add(LLMProviderInstance(
        id=instance_id, type=ProviderType.VLLM, base_url="http://upstream.local", enabled=True, weight=1.0,
        max_concurrency=4, health_status=ProviderHealthStatus.HEALTHY, node_metadata=CAN_CALL_TOOLS,
        created_at=now, updated_at=now,
    ))
    await db.flush()
    db.add(LLMPoolMembership(model_alias=ALIAS, provider_instance_id=instance_id, enabled=True))
    await db.merge(TenantLLMPolicy(
        tenant_id="tenant-a", privacy_mode=PrivacyMode.METADATA_ONLY, allowed_model_aliases=[ALIAS],
        allowed_provider_types=[ProviderType.VLLM.value], rpm_limit=100, tpm_limit=1_000_000,
        pii_config=None, created_at=now, updated_at=now,
    ))
    await db.commit()
    fastapi_app.state.gateway_observability = _Observability()


@pytest.mark.anyio
@needs_postgres
async def test_a_gateway_tool_run_under_bypass_keeps_its_result_after_the_pii_scan(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixture
    limits: list[Any], backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed_tool_model(fastapi_app, db_session)
    backend.put(_profile(max_level="bypass", host_tool_contract=({"name": "update_page", "risk": "write"},)))
    fastapi_app.state.agent_directory.invalidate()
    limits.append(_answering(None, "results"))
    grant = (await _bypass(client)).json()
    assert grant["evidence_level"] == "results"
    _Model(monkeypatch, _call("current_time", {}), _say("It is noon."))
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "messages": [{"role": "user", "content": "What time is it?"}],
    }, headers={**_auth(), **SUPPORTS, "X-Faden-Tool-Mode": "bypass", **EVENTS})
    assert r.status_code == 200, r.text
    row = (await db_session.execute(select(ApprovalGrantCall))).scalar_one()
    assert (row.tool_id, row.side, row.outcome, row.result_at is not None) == ("current_time", "gateway", "ok", True)
    assert row.result and len(row.result) <= 500, "the result as the model got it"


# -- the evidence route ---------------------------------------------------------------------------


def _signed(**claims: Any) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"typ": "grant_evidence", "sub": "auditor-1", "tenant": "tenant-a", "iat": now, "exp": now + 60, **claims},
        settings.jwt_secret, algorithm="HS256",
    )
    return {"X-Faden-Evidence-Grant": token}


@pytest.mark.anyio
@needs_postgres
async def test_the_evidence_route_decrypts_only_what_a_grant_the_backend_signed_names(
    client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
) -> None:
    bind = db_session.bind  # one database for the app and the test: its session's locks end with it
    work = grants.Granted("work", {"classes": ["write"], "targets": [
        {"type": "folder", "identifiers": ["C:\\Docs"], "actions": ["write"]},
    ]}, 30, None, "v3/120m")
    async with AsyncSession(bind, expire_on_commit=False) as session:
        grant = await grants.issue(session, OWNER, work, reason="Renaming drawings", evidence_level="results")
    call = grants.Call(request_id="r1", call_id="c1", tool_id="rename", tool_class="write",
                       place=("folder", "C:\\Docs\\A\\plan.docx"), arguments={"to": "plan-v2.docx"})
    covered = await grants.cover_call(bind, OWNER, call)
    assert covered is not None
    await grants.note_result(bind, covered.record_id, error=True, text="Error: in use")
    route = "/internal/grant-evidence"
    assert (await client.post(route)).status_code == 403, "no grant, nothing read"
    forged = jwt.encode({"typ": "grant_evidence", "sub": "x", "tenant": "tenant-a", "grant": str(grant.id),
                         "part": "calls", "exp": int(time.time()) + 60}, "not-the-secret-but-32-bytes-long!!", algorithm="HS256")
    assert (await client.post(route, headers={"X-Faden-Evidence-Grant": forged})).status_code == 403
    wrong_kind = _signed(grant=str(grant.id), part="calls", typ="chat_erase")
    assert (await client.post(route, headers=wrong_kind)).status_code == 403
    r = await client.post(route, headers=_signed(grant=str(grant.id), part="calls"))
    assert r.status_code == 200, r.text
    found = r.json()
    assert (found["reason"], found["evidence_level"]) == ("Renaming drawings", "results")
    [shown] = found["calls"]
    assert (shown["target"], shown["arguments"], shown["outcome"], shown["result"]) == (
        "C:\\Docs\\A\\plan.docx", {"to": "plan-v2.docx"}, "error", "Error: in use",
    )
    reason = (await client.post(route, headers=_signed(grant=str(grant.id), part="reason"))).json()
    assert reason == {"grant_id": str(grant.id), "reason": "Renaming drawings"}
    other_tenant = _signed(grant=str(grant.id), part="calls", tenant="tenant-b")
    assert (await client.post(route, headers=other_tenant)).status_code == 404
    now = datetime.now(UTC)
    window = {"since": (now - timedelta(hours=1)).isoformat(), "until": (now + timedelta(minutes=1)).isoformat()}
    search = (await client.post(route, headers=_signed(place="c:/docs", **window))).json()
    assert ([c["id"] for c in search["data"]], search["truncated"]) == ([str(covered.record_id)], False)
    assert (await client.post(route, headers=_signed(place="C:\\Other", **window))).json()["data"] == []
    too_wide = {"since": (now - timedelta(days=40)).isoformat(), "until": now.isoformat()}
    assert (await client.post(route, headers=_signed(place="C:\\Docs", **too_wide))).status_code == 422
