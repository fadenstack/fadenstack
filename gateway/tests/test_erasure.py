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
from faden_gateway.db.models.usage import UsageDaily, UsageDailyPerson, UsageErased
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
    [noted] = (await db_session.execute(select(UsageErased))).scalars()
    assert noted.user_id == "u1", "noted, so the rollup removes what of theirs turns up again"
    await erasure.erase_user(db_session, _Store(), "u1")
    assert await _count(db_session, UsageErased) == 1, "erased twice: noted once, with the later time"


async def test_erasing_a_person_takes_what_their_apps_reported(db_session: AsyncSession) -> None:
    """Owner, 16 Nov: the apps' events and the manifests a person reported go with the account (GDPR Art. 17)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from faden_gateway.db.models.gateway import (  # noqa: PLC0415
        AgentClientEvent,
        AgentClientManifest,
        AgentDeviceInventory,
    )

    def event(user: str) -> AgentClientEvent:
        return AgentClientEvent(tenant_id="t1", agent_id="a1", user_id=user, device_id=f"d-{user}",
                                type="tool.executed", data={"tool": "x"})

    def manifest(user: str, sha: str) -> AgentClientManifest:
        return AgentClientManifest(tenant_id="t1", agent_id="a1", sha256=sha, source="app", manifest={},
                                   last_user_id=user, last_device_id=f"d-{user}")

    def device(user: str) -> AgentDeviceInventory:
        return AgentDeviceInventory(agent_id="a1", device_id=f"d-{user}", tenant_id="t1", user_id=user,
                                    plugins=[], mcp_programs=[], reported_at=datetime.now(UTC))

    db_session.add_all([
        event("u1"), event("u1"), event("u2"), manifest("u1", "a" * 64), manifest("u2", "b" * 64),
        device("u1"), device("u2"),
    ])
    await db_session.flush()

    erased = await erasure.erase_user(db_session, _Store(), "u1")

    assert (erased.app_events, erased.app_manifests, erased.devices) == (2, 1, 1)
    assert set((await db_session.execute(select(AgentClientEvent.user_id))).scalars()) == {"u2"}
    assert set((await db_session.execute(select(AgentClientManifest.last_user_id))).scalars()) == {"u2"}
    assert set((await db_session.execute(select(AgentDeviceInventory.user_id))).scalars()) == {"u2"}


async def test_an_account_erase_answers_what_went_of_its_apps_reports(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    from faden_gateway.db.models.gateway import AgentClientEvent  # noqa: PLC0415

    db_session.add(AgentClientEvent(tenant_id="t1", agent_id="a1", user_id="u1", type="tool.executed", data={}))
    await db_session.flush()
    fastapi_app.state.chat_file_store = _Store()
    r = await client.post(
        "/internal/chat-erase", json={"scope": "user", "target": "u1"},
        headers={ERASE_GRANT_HEADER: _grant("user", "u1")},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["app_events"], r.json()["app_manifests"], r.json()["devices"]) == (1, 0, 0)


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

        # An account deleted before 0.7.1 left what its apps reported, even with no chat: found and erased too.
        from faden_gateway.db.models.gateway import AgentClientEvent, AgentClientManifest  # noqa: PLC0415

        session.add_all([
            AgentClientEvent(tenant_id="t1", agent_id="a1", user_id="left-apps", type="tool.executed", data={}),
            AgentClientManifest(tenant_id="t1", agent_id="a1", sha256="c" * 64, source="app", manifest={},
                                last_user_id="left-apps"),
        ])
        await session.flush()
        listed = await chat_orphans.find(session, await known())
        assert list(listed) == ["left-apps"]
        assert (listed["left-apps"]["app_events"], listed["left-apps"]["app_manifests"]) == (1, 1)
        await erasure.erase_user(session, _Store(), "left-apps")
        assert await chat_orphans.find(session, await known()) == {}
        await session.close()
        await tx.rollback()


# ── A file that cannot be deleted (GDPR Art. 17): its row stays, emptied, until the next purge ──


class _Flaky(_Store):
    """A store that cannot delete the files of *failing* (OSError) and has none of *missing*."""

    def __init__(self, *, failing: set[str] = frozenset(), missing: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        super().__init__()
        self.failing, self.missing = set(failing), set(missing)

    async def delete(self, key: str) -> bool:
        if key in self.failing:
            raise OSError("read-only file system")
        if key in self.missing:
            return False
        return await super().delete(key)


async def _file(db: AsyncSession, key: str, *, user: str = "u1", chat: ChatSession | None = None,
                project: ChatProject | None = None) -> ChatAttachment:
    row = ChatAttachment(
        tenant_id="t1", user_id=user, session_id=chat.id if chat else None, project_id=project.id if project else None,
        filename="contract-huber.pdf", content_type="application/pdf", size_bytes=10, storage_key=key,
        extracted_text="Mr Huber's contract", extraction_status=ExtractionStatus.COMPLETED,
        scope=AttachmentScope.PROJECT if project and not chat else AttachmentScope.SESSION,
    )
    db.add(row)
    await db.flush()
    return row


async def _rows(db: AsyncSession) -> dict[str, ChatAttachment]:
    found = await db.execute(select(ChatAttachment).execution_options(populate_existing=True))
    return {row.storage_key: row for row in found.scalars()}


async def _purge_with_a_working_store(db: AsyncSession) -> tuple[erasure.Erased, _Store]:
    store = _Store()
    erased = await erasure.purge_deleted(db, store, deleted_before=datetime.now(UTC) - timedelta(days=30))
    return erased, store


def _waits(row: ChatAttachment) -> None:
    """A row kept for its file: nothing of what it held but the key, under a tombstone."""
    assert row.filename == "" and row.extracted_text is None and row.message_id is None and row.project_id is None


async def _tombstone(db: AsyncSession, chat_id: uuid.UUID) -> ChatSession:
    chat = (await db.execute(
        select(ChatSession).where(ChatSession.id == chat_id).execution_options(populate_existing=True),
    )).scalar_one()
    assert chat.status == SessionStatus.DELETED and chat.deleted_at is not None
    assert chat.deleted_at.replace(tzinfo=UTC) == erasure.RETRY_DUE, "due at once, whatever the retention"
    assert chat.title is None and chat.project_id is None
    return chat


async def test_a_chats_file_that_cannot_be_deleted_keeps_its_row_until_the_next_purge(db_session: AsyncSession) -> None:
    chat = await _chat(db_session)  # its file: t1/<chat>/f
    stuck = await _file(db_session, "t1/stuck", chat=chat)
    store = _Flaky(failing={"t1/stuck"})

    erased = await erasure.erase_session(db_session, store, chat.id)

    assert erased.partial and erased.failed_files == 1 and erased.chats == 0
    assert store.deleted == [f"t1/{chat.id}/f"], "the other file goes"
    rows = await _rows(db_session)
    assert list(rows) == ["t1/stuck"] and rows["t1/stuck"].session_id == chat.id
    _waits(rows["t1/stuck"])
    await _tombstone(db_session, chat.id)
    assert await _count(db_session, ChatMessage) == 0 and await _count(db_session, MemoryFact) == 0

    erased, store = await _purge_with_a_working_store(db_session)

    assert erased.chats == 1 and not erased.partial and store.deleted == ["t1/stuck"]
    assert await _rows(db_session) == {} and await _count(db_session, ChatSession) == 0
    del stuck


async def test_a_projects_files_that_cannot_be_deleted_wait_in_tombstones(db_session: AsyncSession) -> None:
    project = ChatProject(tenant_id="t1", user_id="u1", name="Huber")
    db_session.add(project)
    await db_session.flush()
    chat = await _chat(db_session, project=project)
    await _file(db_session, "t1/chat-stuck", chat=chat, project=project)
    await _file(db_session, "t1/project-stuck", project=project)
    await _file(db_session, "t1/project-ok", project=project)
    store = _Flaky(failing={"t1/chat-stuck", "t1/project-stuck"})

    erased = await erasure.erase_project(db_session, store, project.id)

    assert erased.projects == 1 and erased.failed_files == 2 and erased.partial
    assert sorted(store.deleted) == sorted([f"t1/{chat.id}/f", "t1/project-ok"])
    assert await _count(db_session, ChatProject) == 0, "the project goes"
    rows = await _rows(db_session)
    assert sorted(rows) == ["t1/chat-stuck", "t1/project-stuck"]
    for row in rows.values():
        _waits(row)
        await _tombstone(db_session, row.session_id)
    assert rows["t1/chat-stuck"].session_id == chat.id, "the chat itself is the tombstone"
    assert rows["t1/project-stuck"].session_id != chat.id, "the project's own file gets one of its own"

    erased, store = await _purge_with_a_working_store(db_session)

    assert erased.chats == 2 and sorted(store.deleted) == ["t1/chat-stuck", "t1/project-stuck"]
    assert await _rows(db_session) == {} and await _count(db_session, ChatSession) == 0


async def test_a_single_file_that_cannot_be_deleted_leaves_its_chat(db_session: AsyncSession) -> None:
    chat = await _chat(db_session)
    stuck = await _file(db_session, "t1/stuck", chat=chat)

    erased = await erasure.erase_attachment(db_session, _Flaky(failing={"t1/stuck"}), stuck.id)

    assert erased.failed_files == 1 and erased.files == 0
    rows = await _rows(db_session)
    _waits(rows["t1/stuck"])
    assert rows["t1/stuck"].session_id != chat.id, "out of the chat its person still has"
    await _tombstone(db_session, rows["t1/stuck"].session_id)
    await db_session.refresh(chat)
    assert chat.status == SessionStatus.ACTIVE and f"t1/{chat.id}/f" in rows

    erased, store = await _purge_with_a_working_store(db_session)

    assert store.deleted == ["t1/stuck"] and set(await _rows(db_session)) == {f"t1/{chat.id}/f"}
    assert set((await db_session.execute(select(ChatSession.id))).scalars()) == {chat.id}


async def test_a_persons_files_that_cannot_be_deleted_wait_and_nothing_else_of_theirs_stays(
    db_session: AsyncSession,
) -> None:
    project = ChatProject(tenant_id="t1", user_id="u1", name="Huber")
    db_session.add(project)
    await db_session.flush()
    await _file(db_session, "t1/project-stuck", project=project)
    chat = await _chat(db_session)
    await _file(db_session, "t1/chat-stuck", chat=chat)
    await _file(db_session, "t1/unscoped-stuck")
    await _file(db_session, "t1/unscoped-ok")
    theirs = await _chat(db_session, user="u2")
    store = _Flaky(failing={"t1/project-stuck", "t1/chat-stuck", "t1/unscoped-stuck"})

    erased = await erasure.erase_user(db_session, store, "u1")

    assert erased.failed_files == 3 and erased.projects == 1
    assert sorted(store.deleted) == sorted([f"t1/{chat.id}/f", "t1/unscoped-ok"]), "each file tried once"
    rows = await _rows(db_session)
    assert sorted(rows) == sorted(["t1/project-stuck", "t1/chat-stuck", "t1/unscoped-stuck", f"t1/{theirs.id}/f"])
    for key in ("t1/project-stuck", "t1/chat-stuck", "t1/unscoped-stuck"):
        _waits(rows[key])
        await _tombstone(db_session, rows[key].session_id)
    assert set((await db_session.execute(select(MemoryFact.user_id))).scalars()) == {"u2"}
    assert await _count(db_session, ChatProject) == 0

    erased, store = await _purge_with_a_working_store(db_session)

    assert sorted(store.deleted) == sorted(["t1/project-stuck", "t1/chat-stuck", "t1/unscoped-stuck"])
    assert set(await _rows(db_session)) == {f"t1/{theirs.id}/f"}
    assert set((await db_session.execute(select(ChatSession.user_id))).scalars()) == {"u2"}


async def test_a_missing_file_counts_as_deleted(db_session: AsyncSession) -> None:
    chat = await _chat(db_session)

    erased = await erasure.erase_session(db_session, _Flaky(missing={f"t1/{chat.id}/f"}), chat.id)

    assert erased.chats == 1 and not erased.partial and erased.files == 0
    assert await _rows(db_session) == {} and await _count(db_session, ChatSession) == 0


async def test_without_a_file_store_no_file_counts_as_deleted(db_session: AsyncSession) -> None:
    chat = await _chat(db_session)

    erased = await erasure.erase_session(db_session, None, chat.id)

    assert erased.partial and erased.failed_files == 1
    _waits((await _rows(db_session))[f"t1/{chat.id}/f"])


async def test_files_that_keep_failing_never_hold_the_purge_back(db_session: AsyncSession) -> None:
    stuck = await _chat(db_session)
    await erasure.erase_session(db_session, _Flaky(failing={f"t1/{stuck.id}/f"}), stuck.id)
    due = [await _chat(db_session), await _chat(db_session)]
    for chat in due:
        chat.status, chat.deleted_at = SessionStatus.DELETED, datetime.now(UTC) - timedelta(days=40)
    await db_session.flush()
    store = _Flaky(failing={f"t1/{stuck.id}/f"})
    before = datetime.now(UTC) - timedelta(days=30)

    first = await erasure.purge_deleted(db_session, store, deleted_before=before, limit=2)
    assert first.chats == 2 and not first.partial, "the chats due first, the tombstone after"
    second = await erasure.purge_deleted(db_session, store, deleted_before=before, limit=2)
    assert (second.chats, second.failed_files) == (0, 1), "tried again: a short pass, so the loop stops"
    assert set((await db_session.execute(select(ChatSession.id))).scalars()) == {stuck.id}


async def test_the_erase_apis_say_when_it_is_partial(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    chat = await _chat(db_session)
    fastapi_app.state.chat_file_store = _Flaky(failing={f"t1/{chat.id}/f"})
    r = await client.post("/internal/chat-erase", json={"scope": "session", "target": str(chat.id)},
                          headers={ERASE_GRANT_HEADER: _grant("session", str(chat.id))})
    assert r.status_code == 200, r.text
    assert (r.json()["partial"], r.json()["failed_files"], r.json()["chats"]) == (True, 1, 0)

    project = ChatProject(tenant_id="t1", user_id="u1", name="mine")
    db_session.add(project)
    await db_session.flush()
    inside = await _chat(db_session, project=project)
    fastapi_app.state.chat_file_store = _Flaky(failing={f"t1/{inside.id}/f"})
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(user_id="u1", tenant_id="t1", raw_claims={})
    r = await client.delete(f"/v1/sessions/projects/{project.id}")
    assert r.status_code == 202, r.text
    assert r.json() == {"partial": True, "failed_files": 1}
