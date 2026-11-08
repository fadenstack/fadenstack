"""The admin's list of running grants, with Revoke (sdk/38 §6, D9; sdk/40 A2.5), and a Bypass session's calls.

A grant lives in the gateway's database; the console lists those running -- active or paused, not ended, not
spent -- and an admin ends one from its next call on. What ran under a Bypass session is for the role
``approvals_auditor`` only, each read audited (sdk/09 L-64, L-65; sdk/40 A3).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from faden_backend.db.dao.rbac_dao import APPROVALS_AUDITOR_ROLE, RbacDAO
from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.users import User, current_active_user

#: The gateway's tables, as its migrations leave them (gateway migrations gr4nts, gr4ntx, byp4ss).
_GRANT_TABLE = """
CREATE TABLE approval_grant (
    id uuid PRIMARY KEY, kind varchar(16) NOT NULL, tenant_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL,
    agent_id varchar(64) NOT NULL, agent_version integer, device_id varchar(64), conversation_id varchar(128),
    scope json NOT NULL, issued_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
    max_calls integer, calls_used integer NOT NULL DEFAULT 0, status varchar(16) NOT NULL DEFAULT 'active',
    paused_at timestamptz, revoked_at timestamptz, revoked_by varchar(16), policy_version varchar(64),
    evidence_level varchar(16) NOT NULL DEFAULT 'decisions', shown_text_hash varchar(64),
    client_version varchar(64), reason text
)"""
_CALL_TABLE = """
CREATE TABLE approval_grant_call (
    id uuid PRIMARY KEY, grant_id uuid NOT NULL REFERENCES approval_grant(id) ON DELETE CASCADE,
    request_id varchar(128) NOT NULL, call_id varchar(128) NOT NULL, tool_id varchar(256) NOT NULL,
    target text, arguments text, created_at timestamptz NOT NULL DEFAULT now(), side varchar(8),
    reported_at timestamptz, checked_at timestamptz
)"""
_FLAG_TABLE = """
CREATE TABLE approval_grant_flag (
    id uuid PRIMARY KEY, grant_id uuid NOT NULL REFERENCES approval_grant(id) ON DELETE CASCADE,
    call_id varchar(128) NOT NULL, tool_id varchar(256), kind varchar(16) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
)"""


@pytest.fixture
async def gateway_db(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[AsyncSession]:
    for table in (_GRANT_TABLE, _CALL_TABLE, _FLAG_TABLE):
        await dbsession.execute(text(table))
    saved = getattr(fastapi_app.state, "llm_graph_trace_session_factory", None)
    fastapi_app.state.llm_graph_trace_session_factory = async_sessionmaker(dbsession.bind, expire_on_commit=False)
    yield dbsession
    fastapi_app.state.llm_graph_trace_session_factory = saved


async def _user(fastapi_app: FastAPI, dbsession: AsyncSession, *, superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:10]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    return user


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[User]:
    yield await _user(fastapi_app, dbsession, superuser=True)
    fastapi_app.dependency_overrides.pop(current_active_user, None)


async def _grant(db: AsyncSession, *, minutes: int = 30, status_: str = "active", tenant: str = "default",
                 max_calls: int | None = None, calls_used: int = 0, kind: str = "time") -> str:
    grant_id = uuid.uuid4()
    scope = {"tools": ["update_page"]} if kind == "time" else {
        "classes": ["write"], "targets": [{"type": "document", "identifiers": ["/plan.docx"], "actions": ["write"]}],
    }
    await db.execute(
        text(
            "INSERT INTO approval_grant (id, kind, tenant_id, user_id, agent_id, agent_version, device_id, scope, "
            "expires_at, max_calls, calls_used, status) VALUES (:id, :kind, :tenant, 'u-1', 'a-1', 3, 'device-7', "
            "CAST(:scope AS json), clock_timestamp() + make_interval(mins => :minutes), :max_calls, :calls_used, "
            ":status)",
        ),
        {"id": grant_id, "kind": kind, "tenant": tenant, "scope": json.dumps(scope), "minutes": minutes,
         "max_calls": max_calls, "calls_used": calls_used, "status": status_},
    )
    return str(grant_id)


async def test_the_running_grants_are_listed_and_nothing_ended(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    work = await _grant(gateway_db, kind="work")
    paused = await _grant(gateway_db, status_="paused", minutes=10)
    await _grant(gateway_db, status_="revoked")
    await _grant(gateway_db, minutes=-1)  # expired
    await _grant(gateway_db, max_calls=3, calls_used=3)  # spent
    await _grant(gateway_db, tenant="other")

    r = await client.get("/api/admin/governance/grants")

    assert r.status_code == status.HTTP_200_OK, r.text
    data = r.json()["data"]
    assert sorted(g["id"] for g in data) == sorted([work, paused])
    shown = next(g for g in data if g["id"] == work)
    assert (shown["kind"], shown["user_id"], shown["agent_id"], shown["device_id"]) == ("work", "u-1", "a-1", "device-7")
    assert shown["scope"]["targets"][0]["identifiers"] == ["/plan.docx"]
    assert 1790 <= shown["remaining_s"] <= 1800, "the time left by the database's clock"
    assert next(g for g in data if g["id"] == paused)["status"] == "paused"


async def test_an_admin_revokes_a_running_grant_and_it_says_who_ended_it(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    grant = await _grant(gateway_db)

    r = await client.delete(f"/api/admin/governance/grants/{grant}")

    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    row = (await gateway_db.execute(
        text("SELECT status, revoked_by, revoked_at FROM approval_grant WHERE id = :id"), {"id": grant},
    )).one()
    assert (row.status, row.revoked_by) == ("revoked", "admin") and row.revoked_at is not None
    assert (await client.get("/api/admin/governance/grants")).json()["data"] == []
    again = await client.delete(f"/api/admin/governance/grants/{grant}")
    assert again.status_code == status.HTTP_404_NOT_FOUND, "an ended grant is not ended again"
    unknown = await client.delete(f"/api/admin/governance/grants/{uuid.uuid4()}")
    assert unknown.status_code == status.HTTP_404_NOT_FOUND


async def test_only_an_admin_sees_or_ends_grants(
    client: AsyncClient, fastapi_app: FastAPI, gateway_db: AsyncSession, dbsession: AsyncSession,
) -> None:
    grant = await _grant(gateway_db)
    await _user(fastapi_app, dbsession, superuser=False)
    try:
        assert (await client.get("/api/admin/governance/grants")).status_code == status.HTTP_403_FORBIDDEN
        assert (await client.delete(f"/api/admin/governance/grants/{grant}")).status_code == status.HTTP_403_FORBIDDEN
    finally:
        fastapi_app.dependency_overrides.pop(current_active_user, None)
    row = (await gateway_db.execute(text("SELECT status FROM approval_grant WHERE id = :id"), {"id": grant})).one()
    assert row.status == "active"


async def _bypass_session(db: AsyncSession) -> str:
    """A Bypass grant with three calls: one the device reported, one it did not, and one it reported alone."""
    grant = await _grant(db, kind="bypass")
    for call_id, side, reported in (("c1", "client", True), ("c2", "client", False), ("c3", "gateway", False)):
        await db.execute(
            text(
                "INSERT INTO approval_grant_call (id, grant_id, request_id, call_id, tool_id, arguments, side, "
                "reported_at) VALUES (:id, :grant, 'r1', :call, 'wipe', 'secret-ciphertext', :side, "
                "CASE WHEN :reported THEN clock_timestamp() END)",
            ),
            {"id": uuid.uuid4(), "grant": grant, "call": call_id, "side": side, "reported": reported},
        )
    for call_id, kind in (("c2", "gateway_only"), ("c9", "device_only")):
        await db.execute(
            text("INSERT INTO approval_grant_flag (id, grant_id, call_id, kind) VALUES (:id, :grant, :call, :kind)"),
            {"id": uuid.uuid4(), "grant": grant, "call": call_id, "kind": kind},
        )
    return grant


async def _views(db: AsyncSession, grant: str) -> list[tuple[str, str]]:
    rows = (await db.execute(
        select(AuditEvent.action, AuditEvent.result).where(AuditEvent.target_id == grant),
    )).all()
    return sorted((action, result.value) for action, result in rows)


async def test_a_bypass_session_shows_its_flags_in_the_list(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    grant = await _bypass_session(gateway_db)
    [shown] = (await client.get("/api/admin/governance/grants")).json()["data"]
    assert (shown["id"], shown["kind"], shown["flags"]) == (grant, "bypass", 2)


async def test_only_an_approvals_auditor_reads_what_ran_under_bypass_and_every_read_is_audited(
    client: AsyncClient, fastapi_app: FastAPI, gateway_db: AsyncSession, dbsession: AsyncSession, admin: User,
) -> None:
    grant = await _bypass_session(gateway_db)
    refused = await client.get(f"/api/admin/governance/grants/{grant}/calls")
    assert refused.status_code == status.HTTP_403_FORBIDDEN, "superuser status alone does not give it (L-65)"
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    role = await rbac.get_role_by_name(APPROVALS_AUDITOR_ROLE)
    assert role is not None, "a built-in role"
    await rbac.assign_role(admin.id, role.id)
    await dbsession.flush()
    r = await client.get(f"/api/admin/governance/grants/{grant}/calls")
    assert r.status_code == status.HTTP_200_OK, r.text
    calls = {c["call_id"]: c for c in r.json()["data"]}
    assert {k: (c["side"], c["flag"], c["reported_at"] is not None) for k, c in calls.items()} == {
        "c1": ("client", None, True), "c2": ("client", "gateway_only", False), "c3": ("gateway", None, False),
    }
    assert "arguments" not in calls["c1"], "the arguments stay with the gateway, under its key"
    assert [d["call_id"] for d in r.json()["device_only"]] == ["c9"]
    assert await _views(dbsession, grant) == sorted([
        ("approvals.evidence.view", AuditResult.DENY.value), ("approvals.evidence.view", AuditResult.ALLOW.value),
    ]), "the refusal and the read"
    other = await _grant(gateway_db, kind="work")
    assert (await client.get(f"/api/admin/governance/grants/{other}/calls")).status_code == 404, "Bypass only"
