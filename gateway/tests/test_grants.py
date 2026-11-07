"""Grants: the person lets one agent on one device run what would ask, for a while (sdk/38 §6; sdk/40 A2.1).

What a grant covers is checked without a database: the places, the classes, the floor, the agent's limits. The
budget, the expiry, revocation and pause are the database's clock and one conditional update, so they are tested
on Postgres (``FADEN_TEST_GATEWAY_DSN``, migrated to head) -- as are the API and the coverage table, which run the
agent route end to end on a Postgres session.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

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
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import grants
from faden_gateway.services.gateway import tool_classes as tc
from tests.test_agent_routes import (  # noqa: F401 - backend: the fixture
    AGENT_ID,
    MAINTAINER,
    USER,
    _agent_token,
    _Backend,
    _login_token,
    _profile,
    backend,
)
from tests.test_gateway_knowledge_tools import _call, _Model
from tests.test_gateway_pipeline_combined import ALIAS
from tests.test_governance import EVENTS, _faden, _rule

DSN = os.environ.get("FADEN_TEST_GATEWAY_DSN", "")
needs_postgres = pytest.mark.skipif(
    not DSN, reason="FADEN_TEST_GATEWAY_DSN names no Postgres (postgresql+asyncpg://...)",
)
GRANTS = "/v1/agents/eplan-assistant/grants"
CONVERSATION = {"x-faden-conversation-id": "conv-1"}
UPDATE_PAGE = {"type": "function", "function": {"name": "update_page", "parameters": {"type": "object"}}}


def _call_of(tool_class: str, *, tool: str = "update_page", approval: str | None = None, **fields: Any) -> grants.Call:
    return grants.Call(request_id="r1", call_id="c1", tool_id=tool, tool_class=tool_class, approval=approval, **fields)


# -- what a grant covers, without a database ----------------------------------------------------


def test_a_tools_place_is_the_argument_its_contract_points_at() -> None:
    entry = {"name": "open", "target_argument": "/file/path", "target_type": "document"}
    assert grants.place_of(entry, {"file": {"path": "C:\\Docs\\Plan.docx"}}) == ("document", "C:\\Docs\\Plan.docx")
    assert grants.place_of(entry, {"file": {}}) is None
    assert grants.place_of(entry, {"file": {"path": 3}}) is None, "only text names a place"
    assert grants.place_of({"name": "open"}, {"path": "x"}) is None, "a tool without a place has none"
    assert grants.place_of({**entry, "target_type": "planet"}, {"file": {"path": "x"}}) is None
    assert grants.pointer({"a/b": {"~": [10, 20]}}, "/a~1b/~0/1") == 20, "RFC 6901 escapes"


@pytest.mark.parametrize(("place", "target", "inside"), [
    (("document", "c:/docs/plan.docx"), {"type": "document", "identifiers": ["C:\\Docs\\Plan.docx"]}, True),
    (("document", "C:\\Docs\\Plan2.docx"), {"type": "document", "identifiers": ["C:\\Docs\\Plan.docx"]}, False),
    (("folder", "/srv/share/a/b.txt"), {"type": "folder", "identifiers": ["/srv/share/"]}, True),
    (("folder", "/srv/share"), {"type": "folder", "identifiers": ["/srv/share"]}, True),
    (("folder", "/srv/shared/b.txt"), {"type": "folder", "identifiers": ["/srv/share"]}, False),
    (("folder", "/srv/share/../etc/passwd"), {"type": "folder", "identifiers": ["/srv/share"]}, False),
    (("site", "https://Intranet.example.com/wiki/page"), {"type": "site", "identifiers": ["intranet.example.com"]}, True),
    (("site", "http://intranet.example.com/"), {"type": "site", "identifiers": ["https://intranet.example.com"]}, False),
    (("site", "https://intranet.example.com:8443/"), {"type": "site", "identifiers": ["intranet.example.com"]}, False),
    (("site", "https://intranet.example.com.evil.net/"), {"type": "site", "identifiers": ["intranet.example.com"]},
     False),
    (("app", "WINWORD"), {"type": "app", "identifiers": ["winword"]}, True),
    (("desk", "main"), {"type": "desk", "identifiers": ["main"]}, True),
    (("document", "plan.docx"), {"type": "folder", "identifiers": ["plan.docx"]}, False),
], ids=[
    "document-same", "document-other", "folder-under", "folder-itself", "folder-sibling", "folder-climbs-out",
    "site-origin", "site-other-scheme", "site-other-port", "site-lookalike", "app", "desk", "other-type",
])
def test_a_place_lies_in_a_target_by_identity_path_or_origin(
    place: tuple[str, str], target: dict[str, Any], inside: bool,
) -> None:
    assert grants.place_in(place, target) is inside


WORK = {"classes": ["read", "write"], "targets": [{"type": "document", "identifiers": ["/plan.docx"]}]}


@pytest.mark.parametrize(("kind", "scope", "call", "covered"), [
    ("time", {"tools": ["update_page"]}, _call_of("write"), True),
    ("time", {"tools": ["update_page"]}, _call_of("read"), True),
    ("time", {"tools": ["update_page"]}, _call_of("write", tool="delete_page"), False),
    ("time", {"tools": ["update_page"]}, _call_of("destructive"), False),
    ("time", {"tools": ["update_page"]}, _call_of("unknown"), False),
    ("time", {"tools": ["update_page"]}, _call_of("write", approval="always"), False),
    ("time", {"tools": ["update_page"]}, _call_of("write", approval="ask"), True),
    ("work", WORK, _call_of("write", place=("document", "/plan.docx")), True),
    ("work", WORK, _call_of("write", place=("document", "/other.docx")), False),
    ("work", WORK, _call_of("write"), False),
    ("work", {**WORK, "classes": ["read"]}, _call_of("write", place=("document", "/plan.docx")), False),
    ("work", {"classes": ["read", "write"], "targets": [{**WORK["targets"][0], "actions": ["read"]}]},
     _call_of("write", place=("document", "/plan.docx")), False),
    ("work", WORK, _call_of("destructive", place=("document", "/plan.docx")), False),
    ("plan", {"tools": ["update_page"]}, _call_of("write"), False),
], ids=[
    "time-write", "time-read", "time-other-tool", "time-destructive", "time-unknown", "time-always", "time-ask",
    "work-in-place", "work-other-place", "work-no-place", "work-class-not-granted", "work-action-not-granted",
    "work-destructive", "unbuilt-kind",
])
def test_a_grant_covers_only_reads_and_writes_in_its_scope_and_never_the_floor(
    kind: str, scope: dict[str, Any], call: grants.Call, covered: bool,
) -> None:
    assert grants.in_scope(kind, scope, call) is covered


def _limits(**fields: Any) -> Any:
    return _profile(**fields)


def test_a_grant_is_cut_to_the_agents_limits_and_says_what_was_cut() -> None:
    granted = grants.limit(
        kind="time", tools=["update_page"], classes=[], targets=[], minutes=600, max_calls=None, agent=_limits(),
    )
    assert (granted.minutes, granted.cut, granted.policy_version) == (120, ["minutes"], "v3/120m")
    short = _limits(max_grant_minutes=30, grant_classes=("read",))
    work = grants.limit(
        kind="work", tools=[], classes=["read", "write"], minutes=60, max_calls=5, agent=short,
        targets=[{"type": "folder", "identifiers": ["/srv"], "actions": None}],
    )
    assert (work.minutes, work.cut, work.max_calls) == (30, ["minutes", "classes"], 5)
    assert work.scope == {"classes": ["read"], "targets": [{"type": "folder", "identifiers": ["/srv"], "actions": ["read"]}]}


@pytest.mark.parametrize(("fields", "code"), [
    ({"kind": "plan"}, "grant_kind_unavailable"),
    ({"kind": "bypass"}, "grant_kind_unavailable"),
    ({"kind": "time", "tools": []}, "grant_scope_invalid"),
    ({"kind": "time", "tools": ["a", "b"]}, "grant_scope_invalid"),
    ({"kind": "work", "classes": ["write"], "agent": {"grant_classes": ("read",)}}, "grant_scope_invalid"),
    ({"kind": "work", "classes": ["write"]}, "grant_scope_invalid"),
    ({"kind": "work", "classes": ["write"], "agent": {"standing": ("chat", "time")}}, "grant_not_offered"),
    ({"kind": "time", "tools": ["a"], "agent": {"standing": ("chat",)}}, "grant_not_offered"),
], ids=["plan", "bypass", "time-no-tool", "time-two-tools", "work-class-the-agent-refuses", "work-no-places",
        "work-not-offered", "time-not-offered"])
def test_a_grant_the_agent_does_not_allow_is_refused(fields: dict[str, Any], code: str) -> None:
    agent = _limits(**fields.pop("agent", {}))
    request: dict[str, Any] = {"tools": [], "classes": [], "targets": [], "minutes": 30, "max_calls": None, **fields}
    with pytest.raises(grants.RefusedError) as refused:
        grants.limit(agent=agent, **request)
    assert refused.value.code == code


# -- the budget and the clock, on Postgres -----------------------------------------------------


OWNER = grants.Owner(
    tenant_id="tenant-a", user_id=USER, agent_id=AGENT_ID, agent_version=3, device_id="device-7",
    conversation_id="conv-1",
)
TIME = grants.Granted("time", {"tools": ["update_page"]}, 30, None, "v3/120m")


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(DSN, pool_size=20)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE approval_grant CASCADE"))
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE approval_grant CASCADE"))
        await engine.dispose()


async def _issue(engine: AsyncEngine, granted: grants.Granted = TIME, owner: grants.Owner = OWNER) -> ApprovalGrant:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await grants.issue(session, owner, granted)


async def _used(engine: AsyncEngine, grant_id: uuid.UUID) -> tuple[int, int]:
    async with AsyncSession(engine) as session:
        used = (await session.execute(select(ApprovalGrant.calls_used).where(ApprovalGrant.id == grant_id))).scalar_one()
        calls = (await session.execute(
            select(func.count()).select_from(ApprovalGrantCall).where(ApprovalGrantCall.grant_id == grant_id),
        )).scalar_one()
    return used, calls


async def _sql(engine: AsyncEngine, statement: str, **params: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(statement), params)


@needs_postgres
@pytest.mark.anyio
async def test_two_workers_and_a_callee_share_one_budget_exactly(engine: AsyncEngine) -> None:
    """38 §10: k of many parallel calls run -- from two workers, and a delegated callee counting by the grant's id."""
    budget = 17
    grant = await _issue(engine, replace(TIME, max_calls=budget))
    other_worker = create_async_engine(DSN, pool_size=20)
    try:
        async def worker(bind: AsyncEngine, n: int) -> bool:
            return await grants.cover(bind, OWNER, replace(_call_of("write"), call_id=f"call-{n}")) is not None

        async def callee(n: int) -> bool:
            async with grants.autocommit(engine) as conn:
                found = await grants.load(conn, grant.id)
                assert found is not None
                return await grants.consume(conn, found, replace(_call_of("write"), call_id=f"callee-{n}"))

        results = await asyncio.gather(
            *(worker(engine, n) for n in range(20)), *(worker(other_worker, 100 + n) for n in range(20)),
            *(callee(n) for n in range(20)),
        )
    finally:
        await other_worker.dispose()
    assert sum(results) == budget
    assert await _used(engine, grant.id) == (budget, budget), "each counted call has its record"
    assert await grants.cover(engine, OWNER, _call_of("write")) is None, "spent"


@needs_postgres
@pytest.mark.anyio
async def test_nothing_runs_after_the_expiry_or_a_committed_revocation(engine: AsyncEngine) -> None:
    expiring = await _issue(engine)
    await _sql(engine, "UPDATE approval_grant SET expires_at = now() - interval '1 second' WHERE id = :id", id=expiring.id)
    calls = await asyncio.gather(*(grants.cover(engine, OWNER, replace(_call_of("write"), call_id=str(n)))
                                   for n in range(10)))
    assert calls == [None] * 10
    revoked = await _issue(engine)
    assert await grants.cover(engine, OWNER, _call_of("write")) == revoked.id
    async with AsyncSession(engine) as session:
        assert await grants.revoke(session, revoked.id, tenant_id="tenant-a", user_id=USER) is not None
    assert await grants.cover(engine, OWNER, _call_of("write")) is None, "from the next call on"
    assert await grants.still_usable(engine, revoked.id) is False, "the check after a wait sees it too"
    assert await _used(engine, revoked.id) == (1, 1)


@needs_postgres
@pytest.mark.anyio
async def test_only_the_grants_device_pauses_it_and_the_clock_runs_on(engine: AsyncEngine) -> None:
    grant = await _issue(engine)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        assert await grants.pause(session, grant.id, replace(OWNER, device_id="device-8")) is None
        paused = await grants.pause(session, grant.id, OWNER)
        assert paused is not None
        assert (paused.status, paused.expires_at) == ("paused", grant.expires_at)
    assert await grants.cover(engine, OWNER, _call_of("write")) is None
    async with AsyncSession(engine, expire_on_commit=False) as session:
        resumed = await grants.resume(session, grant.id, OWNER)
        assert resumed is not None
        assert (resumed.status, resumed.paused_at, resumed.expires_at) == ("active", None, grant.expires_at)
    assert await grants.cover(engine, OWNER, _call_of("write")) == grant.id


@needs_postgres
@pytest.mark.anyio
@pytest.mark.parametrize(("owner", "covered"), [
    (OWNER, True),
    (replace(OWNER, conversation_id="conv-2"), True),
    (replace(OWNER, device_id="device-8"), False),
    (replace(OWNER, agent_version=4), False),
    (replace(OWNER, user_id="someone-else"), False),
    (replace(OWNER, agent_id=str(uuid.uuid4())), False),
], ids=["its-own", "any-conversation", "another-device", "a-new-version", "another-person", "another-agent"])
async def test_a_grant_is_used_only_by_its_person_agent_version_and_device(
    engine: AsyncEngine, owner: grants.Owner, covered: bool,
) -> None:
    grant = await _issue(engine, owner=replace(OWNER, conversation_id=None))
    assert (await grants.cover(engine, owner, _call_of("write")) == grant.id) is covered


@needs_postgres
@pytest.mark.anyio
async def test_a_work_grant_keeps_its_conversation_and_records_each_call_with_its_arguments(engine: AsyncEngine) -> None:
    work = grants.Granted("work", WORK, 30, None, "v3/120m")
    grant = await _issue(engine, work)
    call = replace(_call_of("write", place=("document", "/plan.docx")), arguments={"path": "/plan.docx", "text": "Hi"})
    assert await grants.cover(engine, replace(OWNER, conversation_id="conv-2"), call) is None, "another conversation"
    assert await grants.cover(engine, OWNER, call) == grant.id
    time = await _issue(engine)
    assert await grants.cover(engine, OWNER, replace(_call_of("write"), arguments={"page": 3})) == time.id
    async with AsyncSession(engine) as session:
        rows = {r.grant_id: r for r in (await session.execute(select(ApprovalGrantCall))).scalars()}
    assert (rows[grant.id].target, rows[grant.id].arguments) == ("/plan.docx", {"path": "/plan.docx", "text": "Hi"})
    assert rows[time.id].arguments is None, "a time grant records the decision, not the arguments"


@needs_postgres
@pytest.mark.anyio
async def test_a_lookup_that_fails_covers_nothing() -> None:
    broken = create_async_engine(DSN.rsplit("/", 1)[0] + "/no_such_database_for_grants")
    try:
        assert await grants.cover(broken, OWNER, _call_of("write")) is None
        assert await grants.snapshot(broken, OWNER) is None
    finally:
        await broken.dispose()


async def _extend(engine: AsyncEngine, grant_id: uuid.UUID, owner: grants.Owner = OWNER, **fields: Any) -> Any:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await grants.extend(session, grant_id, owner, **{"minutes": 0, "targets": [], "longest_minutes": 120,
                                                                 **fields})


@needs_postgres
@pytest.mark.anyio
async def test_a_grant_is_extended_in_place_never_past_its_longest_span_from_its_start(engine: AsyncEngine) -> None:
    """Owner, 2026-11-07: in place, capped at the longest grant counted from when it was issued."""
    grant = await _issue(engine, replace(TIME, minutes=100))
    longer, cut = await _extend(engine, grant.id, minutes=10, shown_text_hash="a" * 64)
    assert (longer.expires_at - grant.expires_at, cut) == (timedelta(minutes=10), [])
    capped, cut = await _extend(engine, grant.id, minutes=30)
    assert (capped.expires_at - grant.issued_at, cut) == (timedelta(minutes=120), ["minutes"])
    with pytest.raises(grants.RefusedError) as refused:
        await _extend(engine, grant.id, minutes=30)
    assert refused.value.code == "grant_at_longest", "past the cap a new grant needs the full card"
    with pytest.raises(grants.RefusedError):
        await _extend(engine, grant.id, targets=[{"type": "document", "identifiers": ["/a"], "actions": None}])
    async with AsyncSession(engine) as session:
        changes = (await session.execute(
            select(ApprovalGrantChange).order_by(ApprovalGrantChange.created_at),
        )).scalars().all()
    assert [(c.added_seconds, c.shown_text_hash) for c in changes] == [(600, "a" * 64), (600, None)]
    assert changes[1].expires_after - changes[0].expires_before == timedelta(minutes=20)


@needs_postgres
@pytest.mark.anyio
async def test_a_stretch_of_work_gains_places_only_for_what_it_covers(engine: AsyncEngine) -> None:
    grant = await _issue(engine, grants.Granted("work", {**WORK, "classes": ["write"]}, 30, None, "v3/120m"))
    elsewhere = replace(_call_of("write", place=("document", "/other.docx")), call_id="c2")
    assert await grants.cover(engine, OWNER, elsewhere) is None
    wider, _ = await _extend(engine, grant.id, targets=[
        {"type": "document", "identifiers": ["/other.docx"], "actions": ["read", "write"]},
        {"type": "document", "identifiers": ["/plan.docx"], "actions": None},
    ])
    assert wider.scope["targets"][-1] == {"type": "document", "identifiers": ["/other.docx"], "actions": ["write"]}
    assert wider.expires_at == grant.expires_at, "a place alone leaves the end where it was"
    assert await grants.cover(engine, OWNER, elsewhere) == grant.id
    with pytest.raises(grants.RefusedError):
        await _extend(engine, grant.id, targets=[{"type": "site", "identifiers": ["a.example"], "actions": ["read"]}])


@needs_postgres
@pytest.mark.anyio
async def test_only_the_grants_device_extends_a_running_grant(engine: AsyncEngine) -> None:
    grant = await _issue(engine)
    assert await _extend(engine, grant.id, replace(OWNER, device_id="device-8"), minutes=10) == (None, [])
    await _sql(engine, "UPDATE approval_grant SET expires_at = now() - interval '1 second' WHERE id = :id", id=grant.id)
    assert await _extend(engine, grant.id, minutes=10) == (None, []), "an ended grant is not extended"


# -- the API and the coverage table, end to end on Postgres ------------------------------------


#: What the end-to-end tests write, cleared before and after each: the app's own sessions commit on their own
#: connections, as in production, so the test cannot roll them back.
WRITTEN = (
    "approval_grant, llm_gateway_request_log, llm_tool_call_log, llm_gateway_policy_decision, "
    "llm_gateway_tool_rule, agent_client_event, llm_pool_membership, llm_provider_instance, llm_model_alias, "
    "tenant_llm_policy"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """The app's session on Postgres, bound to an engine as in production (overrides conftest's SQLite)."""
    if not DSN:
        pytest.skip("FADEN_TEST_GATEWAY_DSN names no Postgres (postgresql+asyncpg://...)")
    engine = create_async_engine(DSN)
    try:
        await _sql(engine, f"TRUNCATE {WRITTEN} CASCADE")
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
        await _sql(engine, f"TRUNCATE {WRITTEN} CASCADE")
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_rules() -> Iterator[None]:
    gov.tool_rules.invalidate()
    tc.class_rules.invalidate()
    yield
    gov.tool_rules.invalidate()
    tc.class_rules.invalidate()


async def _seed(db: AsyncSession) -> None:
    """A model for tenant-a, as ``test_gateway_pipeline_combined._seed`` -- in the order Postgres's keys need."""
    now = datetime.now(UTC)
    instance_id = uuid.uuid4()
    await db.merge(LLMModelAlias(alias=ALIAS, description="", enabled=True, created_at=now, updated_at=now))
    db.add(LLMProviderInstance(
        id=instance_id, type=ProviderType.REMOTE_OPENAI, base_url="http://upstream.local", enabled=True, weight=1.0,
        max_concurrency=4, health_status=ProviderHealthStatus.HEALTHY, created_at=now, updated_at=now,
    ))
    await db.flush()
    db.add(LLMPoolMembership(model_alias=ALIAS, provider_instance_id=instance_id, enabled=True))
    await db.merge(TenantLLMPolicy(
        tenant_id="tenant-a", privacy_mode=PrivacyMode.METADATA_ONLY, allowed_model_aliases=[ALIAS],
        allowed_provider_types=[ProviderType.REMOTE_OPENAI.value], rpm_limit=100, tpm_limit=1_000_000,
        pii_config=None, created_at=now, updated_at=now,
    ))
    await db.commit()


def _auth(**claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {_agent_token(**claims)}", **CONVERSATION}


async def _grant(client: AsyncClient, **body: Any) -> dict[str, Any]:
    r = await client.post(GRANTS, headers=_auth(), json={"kind": "time", "tools": ["update_page"], "minutes": 30, **body})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.anyio
async def test_a_grant_is_made_listed_paused_and_revoked_by_its_device(
    client: AsyncClient, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    made = await _grant(client, minutes=600)
    assert (made["kind"], made["status"], made["narrowed"], made["calls_used"]) == ("time", "active", ["minutes"], 0)
    assert 7190 <= made["remaining_s"] <= 7200, "cut to the longest grant, by the server's clock"
    assert made["conversation_id"] is None, "a time grant spans conversations unless asked"
    assert (made["device_id"], made["agent_version"]) == ("device-7", 3)
    listed = (await client.get(GRANTS, headers=_auth())).json()
    assert [g["id"] for g in listed["data"]] == [made["id"]] and listed["server_now"]
    other_device = _auth(did="device-8")
    assert (await client.get(GRANTS, headers=other_device)).json()["data"] == []
    assert (await client.post(f"{GRANTS}/{made['id']}/pause", headers=other_device)).status_code == 404
    paused = await client.post(f"{GRANTS}/{made['id']}/pause", headers=_auth())
    assert paused.json()["status"] == "paused"
    assert (await client.post(f"{GRANTS}/{made['id']}/resume", headers=_auth())).json()["status"] == "active"
    revoked = await client.delete(f"{GRANTS}/{made['id']}", headers=other_device)
    assert (revoked.status_code, revoked.json()["status"], revoked.json()["revoked_by"]) == (200, "revoked", "person")
    assert (await client.get(GRANTS, headers=_auth())).json()["data"] == []
    assert (await client.post(f"{GRANTS}/{made['id']}/resume", headers=_auth())).status_code == 404


@pytest.mark.anyio
async def test_the_person_revokes_a_grant_signed_in_anywhere_but_nobody_else_does(
    client: AsyncClient, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    made = await _grant(client)
    login = {"Authorization": f"Bearer {_login_token(MAINTAINER)}"}  # another person, even the agent's maintainer
    assert (await client.delete(f"/v1/grants/{made['id']}", headers=login)).status_code == 404
    assert (await client.delete("/v1/grants/not-an-id", headers=login)).status_code == 404
    mine = {"Authorization": f"Bearer {_login_token(USER)}"}
    r = await client.delete(f"/v1/grants/{made['id']}", headers=mine)
    assert (r.status_code, r.json()["status"]) == (200, "revoked")
    assert (await client.delete(f"/v1/grants/{made['id']}", headers=mine)).json()["status"] == "revoked"


@pytest.mark.anyio
@pytest.mark.parametrize(("headers", "body", "status", "code"), [
    ({"did": ""}, {}, 403, "grant_needs_device"),
    ({}, {"kind": "work", "classes": ["write"], "tools": [],
          "targets": [{"type": "document", "identifiers": ["/plan.docx"]}]}, 422, "grant_conversation_required"),
    ({}, {"conversation": True}, 422, "grant_conversation_required"),
    ({}, {"kind": "plan"}, 422, "grant_kind_unavailable"),
    ({}, {"classes": ["destructive"]}, 422, None),
    ({}, {"minutes": 0}, 422, None),
], ids=["no-device", "work-without-conversation", "time-bound-without-conversation", "unbuilt-kind",
        "destructive-class", "no-time"])
async def test_a_grant_request_the_gateway_refuses(
    headers: dict[str, str], body: dict[str, Any], status: int, code: str | None, client: AsyncClient,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    sent = {"Authorization": f"Bearer {_agent_token(**headers)}"}
    if code != "grant_conversation_required":
        sent |= CONVERSATION
    r = await client.post(GRANTS, headers=sent, json={"kind": "time", "tools": ["update_page"], "minutes": 30, **body})
    assert r.status_code == status, r.text
    if code is not None:
        assert r.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_an_agent_that_offers_no_grants_gets_none(
    client: AsyncClient, fastapi_app: FastAPI, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(standing=("chat",)))
    fastapi_app.state.agent_directory.invalidate()
    r = await client.post(GRANTS, headers=_auth(), json={"kind": "time", "tools": ["update_page"], "minutes": 30})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "grant_not_offered")


@pytest.mark.anyio
async def test_a_grant_is_extended_from_its_device_and_says_what_the_cap_cut(
    client: AsyncClient, fastapi_app: FastAPI, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(max_grant_minutes=60))
    fastapi_app.state.agent_directory.invalidate()
    made = await _grant(client, minutes=30)
    r = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(), json={"minutes": 60})
    assert (r.status_code, r.json()["narrowed"]) == (200, ["minutes"])
    assert 3590 <= r.json()["remaining_s"] <= 3600, "60 minutes from its start, the agent's longest"
    again = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(), json={"minutes": 5})
    assert (again.status_code, again.json()["error"]["code"]) == (422, "grant_at_longest")
    nothing = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(), json={})
    assert nothing.status_code == 422
    other = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(did="device-8"), json={"minutes": 5})
    assert other.status_code == 404


async def _chat(
    client: AsyncClient, mode: str = "ask", *, args: dict[str, Any] | None = None, **claims: Any,
) -> list[dict[str, Any]]:
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "tools": [UPDATE_PAGE], "messages": [{"role": "user", "content": "Fix it."}],
    }, headers={**_auth(**claims), "X-Faden-Tool-Mode": mode, **EVENTS})
    assert r.status_code == 200, r.text
    return _faden(r.text)


def _told(events: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    """What the client was told about its call: used under a grant, to be asked about (with ``rule``), refused."""
    names = {"faden.grant_used": "used", "faden.approval_required": "ask", "faden.tool_denied": "denied"}
    return [
        (names[e["name"]], e["value"].get("rule")) for e in events
        if e.get("name") in names and e["value"].get("toolCallId")
    ]


async def _calls_used(db: AsyncSession, grant_id: str) -> int:
    return (await db.execute(
        select(ApprovalGrant.calls_used).where(ApprovalGrant.id == uuid.UUID(grant_id)),
    )).scalar_one()


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "risk", "floor", "told"), [
    ("ask", "write", None, [("used", None)]),
    ("ask", "read", None, []),
    ("auto", "write", None, []),
    ("read_only", "write", None, [("denied", None)]),
    ("ask", "destructive", None, [("ask", False)]),
    ("auto", "destructive", None, [("ask", False)]),
    ("ask", None, None, [("ask", False)]),
    ("ask", "write", "always", [("ask", True)]),
    ("ask", "write", "admin-rule", [("ask", True)]),
], ids=[
    "ask-write-covered", "ask-read-runs-anyway", "auto-write-runs-anyway", "read-only-write-hidden",
    "ask-destructive", "auto-destructive", "ask-unclassified", "floor-always", "floor-admin-rule",
])
async def test_the_coverage_table_level_by_class_by_floor(
    mode: str, risk: str | None, floor: str | None, told: list[tuple[str, Any]], fastapi_app: FastAPI,
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """sdk/38 §6: covered turns ask into run -- never hidden into run, never past the floor, never destructive."""
    await _seed(db_session)
    contract: dict[str, Any] = {"name": "update_page", **({"risk": risk} if risk else {})}
    if floor == "always":
        contract["approval"] = "always"
    backend.put(_profile(host_tool_contract=(contract,)))
    fastapi_app.state.agent_directory.invalidate()
    if floor == "admin-rule":
        await _rule(db_session, "update_page", "approval_required", "Page changes need your OK.")
    grant = await _grant(client)
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    events = await _chat(client, mode)
    assert _told(events) == told
    assert await _calls_used(db_session, grant["id"]) == (1 if told == [("used", None)] else 0)
    if told == [("used", None)]:
        [used] = [e["value"] for e in events if e.get("name") == "faden.grant_used"]
        assert used == {"toolCallId": "call_1", "toolCallName": "update_page", "grantId": grant["id"]}


@pytest.mark.anyio
async def test_a_work_grant_covers_its_places_only_and_a_spent_grant_asks_again(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session)
    backend.put(_profile(host_tool_contract=(
        {"name": "update_page", "risk": "write", "target_argument": "/path", "target_type": "document"},
    )))
    fastapi_app.state.agent_directory.invalidate()
    grant = await _grant(client, kind="work", tools=[], classes=["write"], max_calls=1,
                         targets=[{"type": "document", "identifiers": ["C:\\Docs\\Plan.docx"]}])
    assert grant["conversation_id"] == "conv-1"
    _Model(monkeypatch, _call("update_page", {"path": "C:\\Docs\\Other.docx"}))
    assert _told(await _chat(client)) == [("ask", False)], "another document"
    _Model(monkeypatch, _call("update_page", {"path": "c:/docs/plan.docx"}))
    assert _told(await _chat(client)) == [("used", None)]
    _Model(monkeypatch, _call("update_page", {"path": "c:/docs/plan.docx"}))
    assert _told(await _chat(client)) == [("ask", False)], "its one call is spent"
    [record] = (await db_session.execute(select(ApprovalGrantCall))).scalars().all()
    assert (record.target, record.arguments, record.tool_id) == (
        "c:/docs/plan.docx", {"path": "c:/docs/plan.docx"}, "update_page",
    )


@pytest.mark.anyio
async def test_a_stream_starts_with_the_devices_grants_only_when_it_has_some(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session)
    backend.put(_profile(host_tool_contract=({"name": "update_page", "risk": "read"},)))
    fastapi_app.state.agent_directory.invalidate()

    def snapshots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e["value"] for e in events if e.get("name") == "faden.grants"]

    _Model(monkeypatch, _call("update_page", {"page": 3}))
    assert snapshots(await _chat(client)) == []
    grant = await _grant(client)
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    [snapshot] = snapshots(await _chat(client))
    assert [g["id"] for g in snapshot["grants"]] == [grant["id"]] and snapshot["serverNow"]
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    assert snapshots(await _chat(client, did="device-8")) == [], "another device's"
