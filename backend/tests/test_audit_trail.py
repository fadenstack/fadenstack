"""The audit trail: chained, kept when a request fails, copied, and every privileged request in it."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.users import User, _signed_in_active_user
from faden_backend.services.audit import chain
from faden_backend.services.audit.context import RequestContext, begin, end


async def _user(db: AsyncSession, *, superuser: bool = True) -> User:
    user = User(
        id=uuid.uuid4(), email=f"admin-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",
        is_active=True, is_verified=True, is_superuser=superuser,
    )
    db.add(user)
    await db.flush()
    return user


# ── The chain, in the request's session (as in the test app) ──────────────────────


@pytest.mark.anyio
async def test_events_are_chained_and_a_change_is_found(dbsession: AsyncSession) -> None:
    dao = AuditDAO(dbsession)
    await chain.start_chain_in(dbsession)
    first = await dao.log("thing.make", "thing", "1", AuditResult.ALLOW)
    second = await dao.log("thing.break", "thing", "1", AuditResult.DENY, severity="high")
    assert second.prev_hash == first.hash and first.hash and len(first.hash) == 64
    assert (await chain.verify(dbsession)).ok

    await dbsession.execute(
        text("UPDATE audit_events SET metadata_json = '{\"forged\": true}' WHERE id = :id"), {"id": first.id},
    )
    dbsession.expire_all()
    verdict = await chain.verify(dbsession)
    assert not verdict.ok
    assert any("changed after it was written" in problem for problem in verdict.problems or [])


@pytest.mark.anyio
async def test_the_chain_start_covers_the_events_written_before_it(dbsession: AsyncSession) -> None:
    for n in range(3):  # as an upgraded install has them: no hash
        dbsession.add(AuditEvent(
            id=uuid.uuid4(), action=f"old.{n}", target_type="x", target_id=str(n), result=AuditResult.ALLOW,
            severity="normal",
        ))
    await dbsession.flush()
    start = await chain.start_chain_in(dbsession)
    assert start is not None and json.loads(start.metadata_json or "{}")["legacy_rows"] == 3
    assert await chain.start_chain_in(dbsession) is None  # once
    assert (await chain.verify(dbsession)).ok

    await dbsession.execute(text("DELETE FROM audit_events WHERE action = 'old.1'"))
    dbsession.expire_all()
    verdict = await chain.verify(dbsession)
    assert not verdict.ok and "from before the chain were changed" in " ".join(verdict.problems or [])


# ── Its own transaction, and the copies (as when the app runs) ─────────────────────


@pytest.fixture
async def own_trail(_engine: AsyncEngine, tmp_path: Path) -> AsyncGenerator[Path]:
    """The trail as the running app has it: committed on its own, copied to a folder; cleaned up after."""
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    chain.configure(factory, tmp_path)
    try:
        yield tmp_path
    finally:
        chain.configure(None, None)
        async with factory() as session:
            await session.execute(delete(AuditEvent))
            await session.execute(text("DELETE FROM \"user\" WHERE email LIKE 'admin-%@example.com'"))
            await session.commit()


@pytest.mark.anyio
async def test_a_refusal_stays_recorded_when_its_request_is_rolled_back(
    _engine: AsyncEngine, own_trail: Path,
) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as request_session:  # the request's own session
        await AuditDAO(request_session).log("container.update", "container", "c1", AuditResult.DENY)
        await request_session.rollback()  # what get_db_session does when the route raises
    async with factory() as session:
        events = (await session.execute(select(AuditEvent))).scalars().all()
    assert [e.action for e in events] == ["container.update"]
    copy = (own_trail / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(copy[0])["hash"] == events[0].hash
    async with factory() as session:
        assert (await chain.verify(session, own_trail)).ok


@pytest.mark.anyio
async def test_who_and_from_where_are_recorded(_engine: AsyncEngine, own_trail: Path) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        user = await _user(session)
        await session.commit()
    token = begin(RequestContext(client_ip="10.1.2.3", user_agent="pytest", request_id="r1", source="console"))
    try:
        async with factory() as session:
            await AuditDAO(session).log("thing.make", "thing", "1", AuditResult.ALLOW, actor_id=user.id)
    finally:
        end(token)
    async with factory() as session:
        event = (await session.execute(select(AuditEvent))).scalar_one()
    assert (event.actor_email, event.client_ip, event.user_agent, event.request_id, event.source) == (
        user.email, "10.1.2.3", "pytest", "r1", "console",
    )


@pytest.mark.anyio
async def test_a_change_in_the_database_is_found_against_the_file_copy(
    _engine: AsyncEngine, own_trail: Path,
) -> None:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        await chain.start_chain_in(session)
        await session.commit()
        await AuditDAO(session).log("a", "x", "1", AuditResult.ALLOW)
        last = await AuditDAO(session).log("b", "x", "2", AuditResult.ALLOW)
    async with factory() as session:  # someone deletes the last event straight from the database
        await session.execute(delete(AuditEvent).where(AuditEvent.id == last.id))
        await session.commit()
    async with factory() as session:
        verdict = await chain.verify(session, own_trail)
    assert not verdict.ok
    assert any("no longer in the database" in problem for problem in verdict.problems or [])


# ── Every privileged request, through the app ──────────────────────────────────────


@pytest.fixture
async def recorded_app(
    fastapi_app: FastAPI, _engine: AsyncEngine, own_trail: Path,
) -> AsyncGenerator[tuple[FastAPI, User]]:
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with factory() as session:
        admin = await _user(session)
        await session.commit()
    # The real "current user" dependency runs, so the request's person is noted.
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin
    yield fastapi_app, admin


async def _events(engine: AsyncEngine) -> list[AuditEvent]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return list((await session.execute(select(AuditEvent).order_by(AuditEvent.seq))).scalars().all())


@pytest.mark.anyio
async def test_reading_the_audit_trail_is_recorded_once_per_ten_minutes(
    recorded_app: tuple[FastAPI, User], _engine: AsyncEngine,
) -> None:
    app, admin = recorded_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            r = await client.get("/api/admin/audit/", params={"limit": 5})
            assert r.status_code == status.HTTP_200_OK, r.text
            assert "X-Total-Count" in r.headers
    reads = [e for e in await _events(_engine) if e.action == "api.get"]
    assert len(reads) == 1
    assert (reads[0].actor_email, reads[0].target_id) == (admin.email, "/api/admin/audit/")


@pytest.mark.anyio
async def test_an_admin_write_without_an_event_of_its_own_is_recorded(
    recorded_app: tuple[FastAPI, User], _engine: AsyncEngine,
) -> None:
    app, admin = recorded_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(f"/api/admin/users/{uuid.uuid4()}/roles", json={"role_ids": []})
    writes = [e for e in await _events(_engine) if e.action == "api.put"]
    assert len(writes) == 1
    event = writes[0]
    assert event.target_id == "/api/admin/users/{user_id}/roles"
    assert event.severity == "high"
    assert json.loads(event.metadata_json or "{}")["status"] == r.status_code
    assert event.result == (AuditResult.ALLOW if r.status_code < 400 else AuditResult.DENY)


@pytest.mark.anyio
async def test_a_failed_sign_in_is_recorded_with_the_address_tried(
    fastapi_app: FastAPI, _engine: AsyncEngine, own_trail: Path,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        r = await client.post(
            "/api/auth/jwt/login", data={"username": "nobody@example.com", "password": "wrong-password-1"},
        )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    failed = [e for e in await _events(_engine) if e.action == "auth.sign_in.failed"]
    assert len(failed) == 1 and failed[0].target_id == "nobody@example.com"
    assert failed[0].result == AuditResult.DENY
