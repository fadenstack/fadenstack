"""Deleted chats are erased after the retention, files first; an administrator's or an account's erase is at once."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import (
    AttachmentScope,
    ChatAttachment,
    ChatMessage,
    ChatProject,
    ChatSession,
    ExtractionStatus,
    LLMGatewayRequestLog,
    LLMToolCallLog,
    MemoryFact,
    SessionStatus,
)
from faden_gateway.db.models.usage import UsageDaily, UsageDailyPerson
from faden_gateway.services.gateway import erasure
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.settings import settings
from faden_gateway.web.api.internal_erase import ERASE_GRANT_HEADER

pytestmark = pytest.mark.anyio


class _Store:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return True


async def _chat(db: AsyncSession, user: str = "u1", *, project: ChatProject | None = None) -> ChatSession:
    chat = ChatSession(tenant_id="t1", user_id=user, title="a chat", project_id=project.id if project else None)
    db.add(chat)
    await db.flush()
    db.add_all([
        ChatMessage(session_id=chat.id, role="user", content="about Mr Huber"),
        ChatAttachment(
            tenant_id="t1", user_id=user, session_id=chat.id, filename="huber.pdf", content_type="application/pdf",
            size_bytes=10, storage_key=f"t1/{chat.id}/f", extraction_status=ExtractionStatus.COMPLETED,
            scope=AttachmentScope.SESSION,
        ),
        MemoryFact(tenant_id="t1", user_id=user, session_id=chat.id, key="k", value="v"),
    ])
    await db.flush()
    return chat


async def _count(db: AsyncSession, model: type) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def test_a_deleted_chat_is_marked_and_cannot_be_brought_back(db_session: AsyncSession) -> None:
    dao = SessionDAO(db_session)
    chat = await _chat(db_session)
    assert await dao.delete_session(session_id=chat.id, tenant_id="t1", user_id="u1")
    await db_session.refresh(chat)
    assert chat.status == SessionStatus.DELETED and chat.deleted_at is not None
    # A PATCH used to restore it: a deleted chat is not changed.
    await dao.update_session(session_id=chat.id, tenant_id="t1", user_id="u1", status=SessionStatus.ACTIVE)
    await db_session.refresh(chat)
    assert chat.status == SessionStatus.DELETED


async def test_a_deleted_chats_files_no_longer_count_against_storage(db_session: AsyncSession) -> None:
    dao = SessionDAO(db_session)
    chat = await _chat(db_session)
    assert (await dao.attachment_stats(tenant_id="t1", user_id="u1"))["total_bytes"] == 10
    await dao.delete_session(session_id=chat.id, tenant_id="t1", user_id="u1")
    assert (await dao.attachment_stats(tenant_id="t1", user_id="u1"))["total_bytes"] == 0


async def test_the_purge_erases_only_what_was_deleted_long_enough_ago(db_session: AsyncSession) -> None:
    old, recent, live = await _chat(db_session), await _chat(db_session), await _chat(db_session)
    old.status = recent.status = SessionStatus.DELETED
    old.deleted_at = datetime.now(UTC) - timedelta(days=40)
    recent.deleted_at = datetime.now(UTC) - timedelta(days=2)
    await db_session.flush()
    store = _Store()

    erased = await erasure.purge_deleted(
        db_session, store, deleted_before=datetime.now(UTC) - timedelta(days=30),
    )

    assert erased.chats == 1 and store.deleted == [f"t1/{old.id}/f"]
    remaining = set((await db_session.execute(select(ChatSession.id))).scalars())
    assert remaining == {recent.id, live.id}
    assert await _count(db_session, ChatMessage) == 2
    assert await _count(db_session, MemoryFact) == 2


async def test_erasing_a_person_takes_their_projects_chats_files_and_memory(db_session: AsyncSession) -> None:
    project = ChatProject(tenant_id="t1", user_id="u1", name="theirs")
    db_session.add(project)
    await db_session.flush()
    await _chat(db_session, project=project)
    await _chat(db_session)
    other = await _chat(db_session, user="u2")
    store = _Store()

    erased = await erasure.erase_user(db_session, store, "u1")

    assert (erased.chats, erased.projects) == (2, 1)
    assert len(store.deleted) == 2
    assert set((await db_session.execute(select(ChatSession.id))).scalars()) == {other.id}
    assert set((await db_session.execute(select(MemoryFact.user_id))).scalars()) == {"u2"}


async def test_erasing_a_person_takes_their_usage_and_leaves_the_sums(db_session: AsyncSession) -> None:
    def logged(user: str) -> LLMGatewayRequestLog:
        return LLMGatewayRequestLog(
            request_id=f"r-{user}", tenant_id="t1", user_id=user, endpoint="/v1/chat/completions",
            status_code=200, latency_ms=1, prompt_tokens=5,
        )

    mine, theirs = logged("u1"), logged("u2")
    db_session.add_all([mine, theirs])
    await db_session.flush()
    day = date(2026, 10, 1)
    db_session.add_all([
        LLMToolCallLog(request_log_id=mine.id, request_id="r-u1", tool_name="search"),
        UsageDailyPerson(day=day, tenant_id="t1", user_id="u1", requests=1, input_tokens=5),
        UsageDailyPerson(day=day, tenant_id="t1", user_id="u2", requests=1, input_tokens=5),
        UsageDaily(day=day, tenant_id="t1", requests=2, input_tokens=10, people=2),
    ])
    await db_session.flush()

    erased = await erasure.erase_user(db_session, _Store(), "u1")

    assert (erased.request_logs, erased.usage_days) == (1, 1)
    assert set((await db_session.execute(select(LLMGatewayRequestLog.user_id))).scalars()) == {"u2"}
    assert await _count(db_session, LLMToolCallLog) == 0
    assert set((await db_session.execute(select(UsageDailyPerson.user_id))).scalars()) == {"u2"}
    assert (await db_session.execute(select(UsageDaily.requests))).scalar_one() == 2, "the sums keep their totals"


def _grant(scope: str, target: str, *, typ: str = "chat_erase") -> str:
    now = int(time.time())
    return jwt.encode(
        {"typ": typ, "scope": scope, "target": target, "iat": now, "exp": now + 60},
        settings.jwt_secret, algorithm="HS256",
    )


async def test_the_backends_erase_needs_its_grant(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    chat = await _chat(db_session)
    fastapi_app.state.chat_file_store = store = _Store()
    body = {"scope": "session", "target": str(chat.id)}

    for headers in ({}, {ERASE_GRANT_HEADER: _grant("session", str(uuid.uuid4()))},
                    {ERASE_GRANT_HEADER: _grant("user", "u1")}):
        r = await client.post("/internal/chat-erase", json=body, headers=headers)
        assert r.status_code == 403, r.text
    assert store.deleted == []

    r = await client.post("/internal/chat-erase", json=body, headers={ERASE_GRANT_HEADER: _grant("session", str(chat.id))})
    assert r.status_code == 200, r.text
    assert r.json()["chats"] == 1 and store.deleted == [f"t1/{chat.id}/f"]


async def test_an_erase_grant_is_not_a_sign_in(fastapi_app: FastAPI, client: AsyncClient) -> None:
    fastapi_app.dependency_overrides.pop(get_auth_context, None)
    r = await client.get("/v1/sessions", headers={"Authorization": f"Bearer {_grant('user', 'u1')}"})
    assert r.status_code == 401


async def test_a_project_deleted_by_its_person_takes_its_chats_files(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    project = ChatProject(tenant_id="t1", user_id="u1", name="mine")
    db_session.add(project)
    await db_session.flush()
    chat = await _chat(db_session, project=project)
    fastapi_app.state.chat_file_store = store = _Store()
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )
    r = await client.delete(f"/v1/sessions/projects/{project.id}")
    assert r.status_code == 204, r.text
    assert store.deleted == [f"t1/{chat.id}/f"]  # the chat's own file, which stayed on disk before


async def test_chat_data_of_accounts_that_no_longer_exist_is_listed_then_erased_on_request(
    db_engine: object, monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    from faden_gateway.tools import chat_orphans  # noqa: PLC0415

    async with db_engine.connect() as conn:  # type: ignore[attr-defined]
        tx = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        await _chat(session, user="gone")
        await _chat(session, user="here")
        await session.flush()

        async def known() -> set[str]:
            return {"here"}

        listed = await chat_orphans.find(session, await known())
        assert list(listed) == ["gone"] and listed["gone"]["chats"] == 1 and listed["gone"]["files"] == 1

        erased = await erasure.erase_user(session, _Store(), "gone")
        assert erased.chats == 1
        assert await chat_orphans.find(session, await known()) == {}
        await session.close()
        await tx.rollback()
