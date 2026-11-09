"""Bypass: the fifth level, held by a server grant ``kind: bypass`` (sdk/38 §4.1, §6; sdk/09 L-57 to L-65; sdk/40 A3).

Without a database: the level narrows to Auto unless the installation and the agent allow it, a Bypass grant covers
every class but never the floor, and the settings are read within their ranges. On Postgres
(``FADEN_TEST_GATEWAY_DSN``): the profile offers Bypass only to clients that announce it, the grant is the record
the device waits for, every call under it is evidence, the kill switch ends it, the device's report is compared,
and the evidence goes after its retention.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from faden_gateway.db.crypto import EncryptedText
from faden_gateway.db.models.gateway import (
    ApprovalGrant,
    ApprovalGrantCall,
    ApprovalGrantChange,
    ApprovalGrantFlag,
)
from faden_gateway.services.gateway import approval_settings, grants
from faden_gateway.services.gateway import tool_classes as tc
from faden_gateway.web.api.v1.agents import client_supports
from tests.test_agent_routes import (  # noqa: F401 - backend: the fixture
    _agent_token,
    _Backend,
    _profile,
    backend,
)
from tests.test_gateway_knowledge_tools import _call, _Model
from tests.test_governance import _rule
from tests.test_grants import (  # noqa: F401 - db_session, engine: the fixtures
    GRANTS,
    OWNER,
    _auth,
    _calls_used,
    _chat,
    _issue,
    _seed,
    _told,
    db_session,
    engine,
    needs_postgres,
)

BYPASS = tc.ToolMode.BYPASS
ON = approval_settings.ApprovalSettings(bypass_allowed=True)
OFF = approval_settings.ApprovalSettings(bypass_allowed=False)
SUPPORTS = {"x-faden-client-supports": "tool_mode.bypass"}


@pytest.fixture(autouse=True)
def _settings() -> Iterator[None]:
    """Bypass allowed unless a test says otherwise, as the backend's settings would say; read again after."""
    approval_settings.remember(ON, ttl_sec=3600)
    yield
    approval_settings.forget()


def _bypass_call(tool_class: str, *, bypass: bool = True, approval: str | None = None) -> grants.Call:
    return grants.Call(
        request_id="r1", call_id="c1", tool_id="wipe", tool_class=tool_class, approval=approval, bypass=bypass,
    )


# -- the level, without a database -------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(("allowed", "limit", "ee", "mode"), [
    (True, BYPASS, None, BYPASS),
    (False, BYPASS, None, tc.ToolMode.AUTO),
    (True, tc.ToolMode.AUTO, None, tc.ToolMode.AUTO),
    (True, BYPASS, "auto", tc.ToolMode.AUTO),
    (True, BYPASS, "ask", tc.ToolMode.ASK),
    (True, None, None, tc.ToolMode.AUTO),
], ids=["allowed", "installation-off", "agent-up-to-auto", "ee-ceiling-auto", "ee-ceiling-ask", "no-agent"])
async def test_bypass_holds_only_where_the_installation_the_agent_and_the_ceilings_allow_it(
    allowed: bool, limit: tc.ToolMode | None, ee: str | None, mode: tc.ToolMode,
) -> None:
    async def ceiling(_: tc.CeilingContext) -> str:
        return ee or "bypass"

    effective = await tc.effective(
        BYPASS, [ceiling] if ee else [], tc.CeilingContext(tenant_id="t", user_id="u"),
        limit=limit, bypass_allowed=allowed,
    )
    assert effective.mode is mode
    assert effective.chosen is BYPASS


def test_bypass_alone_runs_what_auto_runs_and_no_conversation_starts_in_it() -> None:
    for tool_class in tc.ToolClass:
        tool = tc.Classified("t", tool_class, "agent")
        assert tc.decide(BYPASS, tool) is tc.decide(tc.ToolMode.AUTO, tool), tool_class
    default, highest = _profile(default_level="bypass", max_level="bypass").levels()
    assert (default, highest) == (tc.ToolMode.ASK, BYPASS), "sdk/38 D2: never a default"
    assert tc.narrowest(BYPASS, tc.ToolMode.AUTO) is tc.ToolMode.AUTO


@pytest.mark.parametrize(("call", "covered"), [
    (_bypass_call("destructive"), True),
    (_bypass_call("unknown"), True),
    (_bypass_call("write"), True),
    (_bypass_call("read"), True),
    (_bypass_call("destructive", bypass=False), False),
    (_bypass_call("destructive", approval="always"), False),
    (_bypass_call("write", approval="ask"), True),
], ids=["destructive", "unclassified", "write", "read", "chat-not-in-bypass", "floor-always", "author-ask"])
def test_a_bypass_grant_covers_every_class_while_the_chat_is_in_bypass_never_the_floor(
    call: grants.Call, covered: bool,
) -> None:
    assert grants.in_scope("bypass", {"classes": list(grants.BYPASS_CLASSES)}, call) is covered
    assert grants.in_scope("time", {"tools": ["wipe"]}, call) is (covered and call.tool_class in ("read", "write"))


def test_a_bypass_grant_is_made_only_where_allowed_and_cut_to_the_shortest_longest_grant() -> None:
    agent = _profile(max_level="bypass", max_grant_minutes=90)
    request: dict[str, Any] = {"tools": [], "classes": [], "targets": [], "minutes": 600, "max_calls": None}
    with pytest.raises(grants.RefusedError) as refused:
        grants.limit(kind="bypass", agent=agent, **request)
    assert refused.value.code == "bypass_not_allowed"
    made = grants.limit(kind="bypass", agent=agent, tenant_longest=60, bypass_allowed=True, **request)
    assert (made.kind, made.minutes, made.cut, made.policy_version) == ("bypass", 60, ["minutes"], "v3/60m/bypass")
    assert made.scope == {"classes": ["read", "write", "destructive", "unknown"]}
    assert grants.EVIDENCE["bypass"] == "calls", "L-60: arguments by default"
    longer = grants.limit(kind="bypass", agent=agent, tenant_longest=600, bypass_allowed=True, **request)
    assert longer.minutes == 90, "the agent's own is shorter"


@pytest.mark.parametrize(("values", "expected"), [
    ({}, OFF),
    ({"bypass_allowed": "true"}, OFF),
    ({"bypass_allowed": True, "longest_grant_minutes": 5, "bypass_retention_days": 9999},
     approval_settings.ApprovalSettings(True, 15, 1095)),
    ({"longest_grant_minutes": "45", "bypass_retention_days": "x"}, approval_settings.ApprovalSettings(False, 45, 365)),
], ids=["defaults", "only-true-turns-it-on", "ends-of-the-ranges", "numbers-as-text"])
def test_the_settings_are_read_within_their_ranges_and_bypass_stays_off_unless_on(
    values: dict[str, Any], expected: approval_settings.ApprovalSettings,
) -> None:
    assert approval_settings.from_values(values) == expected


def test_before_its_first_read_a_worker_has_bypass_off() -> None:
    approval_settings.forget()
    assert approval_settings.last() == OFF


# -- the profile and the grant, end to end on Postgres ------------------------------------------


async def _profile_of(client: AsyncClient, **headers: str) -> dict[str, Any]:
    r = await client.get(
        "/v1/agents/eplan-assistant/profile", headers={"Authorization": f"Bearer {_agent_token()}", **headers},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
@needs_postgres
@pytest.mark.parametrize(("settings", "supports", "max_level", "shown"), [
    (ON, False, "bypass", ("auto", None)),
    (ON, True, "bypass", ("bypass", True)),
    (OFF, True, "bypass", ("auto", False)),
    (ON, True, "auto", ("auto", False)),
], ids=["old-client", "announced-and-allowed", "installation-off", "agent-up-to-auto"])
async def test_the_profile_offers_bypass_only_to_clients_that_announce_it_and_only_where_allowed(
    settings: approval_settings.ApprovalSettings, supports: bool, max_level: str, shown: tuple[str, Any],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    approval_settings.remember(settings, ttl_sec=3600)
    backend.put(_profile(max_level=max_level))
    fastapi_app.state.agent_directory.invalidate()
    profile = await _profile_of(client, **(SUPPORTS if supports else {}))
    offered = profile["approvals"].get("bypass")
    assert (profile["tool_modes"]["max"], offered["allowed"] if offered else None) == shown
    assert ("bypass" in profile["approvals"]) is supports, "old clients' wire unchanged"


def test_a_client_names_what_it_supports_in_one_header() -> None:
    request = SimpleNamespace(headers={"x-faden-client-supports": " Tool_Mode.Bypass , other ,"})
    assert client_supports(request) == frozenset({"tool_mode.bypass", "other"})  # type: ignore[arg-type]


async def _bypass(client: AsyncClient, **body: Any) -> Any:
    return await client.post(GRANTS, headers={**_auth(), **SUPPORTS}, json={"kind": "bypass", "minutes": 30, **body})


@pytest.mark.anyio
@needs_postgres
async def test_a_bypass_grant_is_the_record_the_device_waits_for(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(max_level="auto"))
    fastapi_app.state.agent_directory.invalidate()
    refused = await _bypass(client)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "bypass_not_allowed")
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    unbound = await client.post(
        GRANTS, headers={"Authorization": f"Bearer {_agent_token()}"}, json={"kind": "bypass", "minutes": 30},
    )
    assert (unbound.status_code, unbound.json()["error"]["code"]) == (422, "grant_conversation_required")
    made = await _bypass(client, minutes=600, reason="Renaming 400 drawings", client_version="Fadenstack.Agents/0.3.0")
    assert made.status_code == 201, made.text
    grant = made.json()
    assert (grant["kind"], grant["conversation_id"], grant["narrowed"]) == ("bypass", "conv-1", ["minutes"])
    assert 7190 <= grant["remaining_s"] <= 7200, "the installation's longest grant"
    row = (await db_session.execute(select(ApprovalGrant).where(ApprovalGrant.id == uuid.UUID(grant["id"])))).scalar_one()
    assert (row.client_version, row.reason, row.evidence_level, row.policy_version) == (
        "Fadenstack.Agents/0.3.0", "Renaming 400 drawings", "calls", "v3/120m/bypass",
    )
    assert isinstance(ApprovalGrant.__table__.c.reason.type, EncryptedText), "the reason is encrypted at rest"
    approval_settings.remember(OFF, ttl_sec=3600)
    off = await _bypass(client)
    assert (off.status_code, off.json()["error"]["code"]) == (403, "bypass_not_allowed")


@pytest.mark.anyio
@needs_postgres
async def test_a_bypass_grant_is_never_extended_more_time_is_a_new_bypass(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """Owner, 2026-11-08 (sdk/09 L-58): a Bypass grant runs for the time its confirmation chose."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    made = (await _bypass(client)).json()
    grant_id = uuid.UUID(made["id"])
    shown = select(ApprovalGrant.expires_at, ApprovalGrant.scope, ApprovalGrant.status).where(ApprovalGrant.id == grant_id)
    before = (await db_session.execute(shown)).one()
    for body in ({"minutes": 30}, {"minutes": 5, "targets": [{"type": "document", "identifiers": ["/plan.docx"]}]}):
        r = await client.post(f"{GRANTS}/{made['id']}/extend", headers=_auth(), json=body)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "bypass_not_extendable"
    assert (await db_session.execute(shown)).one() == before, "the grant as it was"
    changes = (await db_session.execute(
        select(ApprovalGrantChange).where(ApprovalGrantChange.grant_id == grant_id),
    )).scalars().all()
    assert changes == [], "no extension recorded"
    again = await _bypass(client)
    assert again.status_code == 201, "more time is a new Bypass, with its confirmation"


@pytest.mark.anyio
@needs_postgres
@pytest.mark.parametrize(("mode", "risk", "floor", "told", "recorded"), [
    ("bypass", "destructive", None, [("used", None)], 1),
    ("bypass", None, None, [("used", None)], 1),
    ("bypass", "read", None, [], 1),
    ("bypass", "write", None, [], 1),
    ("bypass", "destructive", "always", [("ask", True)], 0),
    ("bypass", "destructive", "admin-rule", [("ask", True)], 0),
    ("auto", "destructive", None, [("ask", False)], 0),
    ("ask", "write", None, [("ask", False)], 0),
], ids=[
    "destructive-runs", "unclassified-runs", "read-recorded", "write-recorded", "floor-always", "floor-admin-rule",
    "chat-in-auto", "chat-in-ask",
])
async def test_under_bypass_the_mode_asks_nothing_every_call_is_evidence_and_the_floor_still_asks(
    mode: str, risk: str | None, floor: str | None, told: list[tuple[str, Any]], recorded: int,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session)
    contract: dict[str, Any] = {"name": "update_page", **({"risk": risk} if risk else {})}
    if floor == "always":
        contract["approval"] = "always"
    backend.put(_profile(max_level="bypass", host_tool_contract=(contract,)))
    fastapi_app.state.agent_directory.invalidate()
    if floor == "admin-rule":
        await _rule(db_session, "update_page", "approval_required", "Page changes need your OK.")
    grant = (await _bypass(client)).json()
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    assert _told(await _chat(client, mode)) == told
    assert await _calls_used(db_session, grant["id"]) == recorded
    rows = (await db_session.execute(select(ApprovalGrantCall))).scalars().all()
    assert [(r.tool_id, r.arguments, r.side) for r in rows] == [("update_page", {"page": 3}, "client")] * recorded


@pytest.mark.anyio
@needs_postgres
async def test_with_the_switch_off_bypass_covers_nothing_and_the_kill_switch_ends_it(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session)
    backend.put(_profile(max_level="bypass", host_tool_contract=({"name": "update_page", "risk": "destructive"},)))
    fastapi_app.state.agent_directory.invalidate()
    grant = (await _bypass(client)).json()
    approval_settings.remember(OFF, ttl_sec=3600)
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    assert _told(await _chat(client, "bypass")) == [("ask", False)], "Bypass is Auto now"
    approval_settings.remember(ON, ttl_sec=0)

    async def read(_: str | None = None) -> approval_settings.ApprovalSettings:
        return OFF

    monkeypatch.setattr(approval_settings, "read", read)
    await approval_settings.current(db_session.bind)  # this worker saw it on, and now off
    row = (await db_session.execute(
        select(ApprovalGrant.status, ApprovalGrant.revoked_by).where(ApprovalGrant.id == uuid.UUID(grant["id"])),
    )).one()
    assert tuple(row) == ("revoked", "kill_switch")


@pytest.mark.anyio
@needs_postgres
async def test_the_device_reads_why_its_bypass_ended_and_the_list_applies_the_kill_switch_first(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 - the fixture
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """The device's marker goes when the server ends a Bypass, and the chat says why (sdk/09 L-59, L-63)."""
    backend.put(_profile(max_level="bypass"))
    fastapi_app.state.agent_directory.invalidate()
    grant = (await _bypass(client)).json()
    running = await client.get(f"{GRANTS}/{grant['id']}", headers=_auth())
    assert (running.status_code, running.json()["status"], running.json()["revoked_by"]) == (200, "active", None)
    approval_settings.remember(ON, ttl_sec=0)

    async def read(_: str | None = None) -> approval_settings.ApprovalSettings:
        return OFF

    monkeypatch.setattr(approval_settings, "read", read)
    listed = await client.get(GRANTS, headers=_auth())
    assert (listed.status_code, listed.json()["data"]) == (200, []), "the kill switch applied before the list"
    ended = await client.get(f"{GRANTS}/{grant['id']}", headers=_auth())
    assert (ended.status_code, ended.json()["status"], ended.json()["revoked_by"]) == (200, "revoked", "kill_switch")
    assert ended.json()["evidence_level"] == "calls"
    other_device = await client.get(f"{GRANTS}/{grant['id']}", headers=_auth(did="device-8"))
    other_person = await client.get(f"{GRANTS}/{grant['id']}", headers=_auth(sub="someone-else"))
    unknown = await client.get(f"{GRANTS}/{uuid.uuid4()}", headers=_auth())
    assert [r.status_code for r in (other_device, unknown)] == [404, 404], "ASVS 5.0 8.2.2"
    assert other_person.status_code in (403, 404), "an unknown person is refused at the sign-in already"


# -- the grant's store, on Postgres -------------------------------------------------------------


BYPASS_GRANT = grants.Granted("bypass", {"classes": list(grants.BYPASS_CLASSES)}, 30, None, "v3/120m/bypass")


def _client_call(call_id: str, tool_class: str = "destructive") -> grants.Call:
    return grants.Call(
        request_id="r1", call_id=call_id, tool_id="wipe", tool_class=tool_class, bypass=True, side="client",
    )


@needs_postgres
@pytest.mark.anyio
async def test_the_devices_report_is_compared_with_the_gateways_records_both_ways(engine: AsyncEngine) -> None:  # noqa: F811 - the fixture
    grant = await _issue(engine, BYPASS_GRANT)
    for call_id in ("c1", "c2", "c3"):
        assert await grants.cover(engine, OWNER, _client_call(call_id)) == grant.id
    async with AsyncSession(engine, expire_on_commit=False) as session:
        found = await grants.report(session, grant.id, OWNER, ["c1", "c2", "c9"])
        assert found == grants.Report(matched=2, device_only=["c9"], gateway_only=["c3"])
        again = await grants.report(session, grant.id, OWNER, ["c1"])
        assert again == grants.Report(matched=0, device_only=[], gateway_only=[]), "each call compared once"
        flags = (await session.execute(select(ApprovalGrantFlag.call_id, ApprovalGrantFlag.kind))).all()
        assert sorted(map(tuple, flags)) == [("c3", "gateway_only"), ("c9", "device_only")]
        stranger = replace(OWNER, device_id="device-8")
        assert await grants.report(session, grant.id, stranger, ["c1"]) is None, "only its device reports"


@needs_postgres
@pytest.mark.anyio
async def test_a_delegated_callee_never_uses_a_bypass_grant(engine: AsyncEngine) -> None:  # noqa: F811 - the fixture
    bypass = await _issue(engine, BYPASS_GRANT)
    async with grants.autocommit(engine) as conn:
        assert await grants.load(conn, bypass.id) is None, "sdk/09 L-58"


@needs_postgres
@pytest.mark.anyio
async def test_bypass_evidence_goes_after_its_retention_and_running_grants_end_by_the_kill_switch(
    engine: AsyncEngine,  # noqa: F811 - the fixture
) -> None:
    old = await _issue(engine, BYPASS_GRANT)
    recent = await _issue(engine, BYPASS_GRANT)
    work = await _issue(engine, grants.Granted("time", {"tools": ["update_page"]}, 30, None, "v3/120m"))
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE approval_grant SET expires_at = :at, issued_at = :at WHERE id IN (:old, :work)"),
            {"at": datetime.now(UTC) - timedelta(days=400), "old": old.id, "work": work.id},
        )
    assert await grants.purge_bypass(engine, 365) == 1
    async with AsyncSession(engine) as session:
        left = set((await session.execute(select(ApprovalGrant.id))).scalars().all())
    assert left == {recent.id, work.id}, "only Bypass evidence, only past its time"
    assert await grants.end_bypass(engine) == 1
    assert await grants.end_bypass(engine) == 0, "idempotent"
