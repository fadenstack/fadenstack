"""A chat page's reply runs on its own: the page may leave and come back.

The reply was produced inside the HTTP response, so a page that went away took
it with it: the model's request was aborted and what had arrived was saved as
if it were the whole answer. Now it is a task of the gateway's; the response
only reads it, and a stop is a request of its own.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatMessage, ChatSession, ProviderType
from faden_gateway.services.gateway import service as service_module
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.settings import settings
from tests.test_gateway_pipeline_combined import ALIAS, _chunk, _Observability, _seed, _session, _token
from tests.test_gateway_stream_finish import ANSWER, _as_a_client_that_leaves

PIECES = [ANSWER[i:i + 4] for i in range(0, len(ANSWER), 4)]


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _body(sid: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "model": ALIAS, "stream": True, "session_id": str(sid), "background": True,
        "messages": [{"role": "user", "content": "hi"}], **extra,
    }


def _slow_model(monkeypatch: pytest.MonkeyPatch, *, seconds: float = 0.02) -> dict[str, bool]:
    """The model answers ANSWER a piece at a time, *seconds* apart; ``closed`` once its stream is."""
    seen = {"closed": False}

    async def completion(self: LLMAdapter, **_: Any) -> Any:
        async def pieces() -> AsyncIterator[bytes]:
            try:
                for piece in PIECES:
                    await asyncio.sleep(seconds)
                    yield _chunk(piece)
                yield b"data: [DONE]\n\n"
            finally:
                seen["closed"] = True

        return pieces()

    monkeypatch.setattr(LLMAdapter, "completion", completion)
    return seen


async def _replies(db: AsyncSession, sid: uuid.UUID, until: Any, seconds: float = 3.0) -> list[ChatMessage]:
    """The chat's messages once *until* holds, read on a session of the test's own.

    It ends with a commit: closed without one, a session joined to the test's
    transaction rolls that transaction back.
    """
    async with AsyncSession(db.bind, expire_on_commit=False) as own:
        try:
            for _ in range(int(seconds / 0.01)):
                rows = list((await own.execute(
                    select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at),
                )).scalars().all())
                if until(rows):
                    return rows
                own.expire_all()
                await asyncio.sleep(0.01)
        finally:
            await own.commit()
    raise AssertionError(f"not reached: {[(m.role, m.status, m.content) for m in rows]}")


def _ended(rows: list[ChatMessage]) -> bool:
    return len(rows) == 2 and rows[-1].status != "running"


@pytest.fixture(autouse=True)
async def _replies_end_before_the_database(db_session: AsyncSession) -> AsyncIterator[None]:
    """A reply's task outlives its request; here it ends before the test's database does."""
    yield
    running = list(service_module._BACKGROUND)  # noqa: SLF001
    if running:
        await asyncio.wait(running, timeout=5)


@pytest.fixture
async def chat(fastapi_app: FastAPI, db_session: AsyncSession) -> uuid.UUID:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _session(db_session)
    await db_session.commit()
    return sid


# ── Leaving and coming back ───────────────────────────────────────


@pytest.mark.anyio
async def test_a_reply_is_written_to_its_end_after_the_page_left(
    fastapi_app: FastAPI, chat: uuid.UUID, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _slow_model(monkeypatch, seconds=0)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])

    reply = (await _replies(db_session, chat, _ended))[-1]
    assert (reply.status, reply.content) == ("complete", ANSWER), "the whole answer, not what had arrived"
    assert seen["closed"]


@pytest.mark.anyio
async def test_a_page_that_comes_back_reads_the_whole_reply(
    fastapi_app: FastAPI, chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slow_model(monkeypatch, seconds=0.005)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])

    back = await client.get(f"/v1/sessions/{chat}/stream", headers=_auth())
    events = [line.removeprefix("data: ") for line in back.text.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    text = "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])
    assert text == ANSWER
    await _replies(db_session, chat, _ended)


# ── Stopping ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_stopped_reply_keeps_what_it_wrote_and_the_model_call_is_closed(
    fastapi_app: FastAPI, chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _slow_model(monkeypatch, seconds=0.05)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])
    running = (await _replies(db_session, chat, lambda rows: len(rows) == 2))[-1]

    r = await client.post(f"/v1/sessions/{chat}/turns/{running.id}/stop", headers=_auth())
    assert r.json() == {"stopping": True}

    reply = (await _replies(db_session, chat, _ended))[-1]
    assert reply.status == "stopped"
    assert reply.content and ANSWER.startswith(reply.content) and reply.content != ANSWER
    assert seen["closed"], "the model's request is closed, not left to run"


@pytest.mark.anyio
async def test_a_reply_that_is_not_running_here_is_not_stopped(chat: uuid.UUID, client: AsyncClient) -> None:
    r = await client.post(f"/v1/sessions/{chat}/turns/{uuid.uuid4()}/stop", headers=_auth())
    assert r.json() == {"stopping": False}


@pytest.mark.anyio
async def test_someone_else_s_reply_cannot_be_stopped(client: AsyncClient) -> None:
    r = await client.post(f"/v1/sessions/{uuid.uuid4()}/turns/{uuid.uuid4()}/stop", headers=_auth())
    assert r.status_code == 404


# ── A gateway that goes away ──────────────────────────────────────


@pytest.mark.anyio
async def test_a_reply_the_gateway_dropped_is_interrupted(
    fastapi_app: FastAPI, chat: uuid.UUID, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slow_model(monkeypatch, seconds=0.05)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])
    runners = [t for t in service_module._BACKGROUND if "_run_turn" in t.get_coro().__qualname__]  # noqa: SLF001
    assert len(runners) == 1
    runners[0].cancel()

    reply = (await _replies(db_session, chat, _ended))[-1]
    assert reply.status == "interrupted"
    assert reply.error == "The server stopped while this reply was being written."


# ── Limits ────────────────────────────────────────────────────────


async def _running_in(db: AsyncSession, sid: uuid.UUID, *, beat: timedelta = timedelta(0)) -> None:
    db.add(ChatMessage(
        session_id=sid, role="assistant", content="", status="running",
        updated_at=datetime.now(timezone.utc) - beat,
    ))
    await db.commit()


@pytest.mark.anyio
async def test_one_reply_at_a_time_in_a_chat(chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession) -> None:
    await _running_in(db_session, chat)
    r = await client.post("/v1/chat/completions", headers=_auth(), json=_body(chat))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "turn_running")


@pytest.mark.anyio
async def test_a_few_replies_at_once_per_user(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "chat_turns_per_user", 1)
    other = ChatSession(tenant_id="tenant-a", user_id="user-1", title="other")
    db_session.add(other)
    await db_session.flush()
    await _running_in(db_session, other.id)

    r = await client.post("/v1/chat/completions", headers=_auth(), json=_body(chat))
    assert (r.status_code, r.json()["error"]["code"]) == (429, "too_many_turns")


@pytest.mark.anyio
async def test_a_reply_whose_heartbeat_stopped_does_not_count(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway that went away left it running; it does not hold the chat."""
    await _running_in(db_session, chat, beat=timedelta(minutes=5))
    _slow_model(monkeypatch, seconds=0)
    r = await client.post("/v1/chat/completions", headers=_auth(), json=_body(chat))
    assert r.status_code == 200
