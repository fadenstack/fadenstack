"""A reply is read, stopped and seen from any gateway worker, not only the one writing it.

The gateway runs several workers. A page that came back reached any of them,
and only the one writing the reply had it: on .61 (two workers) a returning
page was told "no response" half the time while the reply was still being
written. Another worker is played here by a second stream buffer on the same
Redis.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import TURN_INTERRUPTED_ERROR, ChatMessage
from faden_gateway.services.gateway import turn_log as turn_log_module
from faden_gateway.services.gateway.stream_buffer import StreamBuffer
from faden_gateway.services.gateway.turn_log import TurnLog
from tests.test_chat_turns_background import (  # noqa: F401 - fixtures
    ANSWER,
    PIECES,
    _auth,
    _body,
    _ended,
    _replies,
    _replies_end_before_the_database,
    _slow_model,
    chat,
)
from tests.test_gateway_stream_finish import _as_a_client_that_leaves


def _another_worker(fastapi_app: FastAPI, pool: ConnectionPool) -> None:
    """From here on, requests reach a worker that is not writing the reply."""
    fastapi_app.state.stream_buffer = StreamBuffer(log=TurnLog(pool))


def _text(body: str) -> str:
    events = [line.removeprefix("data: ") for line in body.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])


@pytest.mark.anyio
async def test_a_page_that_comes_back_to_another_worker_reads_the_whole_reply(
    fastapi_app: FastAPI, chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession,  # noqa: F811
    fake_redis_pool: ConnectionPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slow_model(monkeypatch, seconds=0.02)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])
    _another_worker(fastapi_app, fake_redis_pool)

    status = await client.get(f"/v1/sessions/{chat}/stream/status", headers=_auth())
    assert status.json() == {"active": True}
    back = await client.get(f"/v1/sessions/{chat}/stream", headers=_auth())
    assert back.status_code == 200
    assert _text(back.text) == ANSWER
    await _replies(db_session, chat, _ended)


@pytest.mark.anyio
async def test_a_stop_asked_of_another_worker_stops_the_reply(
    fastapi_app: FastAPI, chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession,  # noqa: F811
    fake_redis_pool: ConnectionPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _slow_model(monkeypatch, seconds=0.05)
    await _as_a_client_that_leaves(fastapi_app, _body(chat), leaves_after=f'"{PIECES[0]}"', seen=[])
    running = (await _replies(db_session, chat, lambda rows: len(rows) == 2))[-1]
    _another_worker(fastapi_app, fake_redis_pool)

    r = await client.post(f"/v1/sessions/{chat}/turns/{running.id}/stop", headers=_auth())
    assert r.json() == {"stopping": True}

    reply = (await _replies(db_session, chat, _ended))[-1]
    assert reply.status == "stopped" and reply.content != ANSWER
    assert seen["closed"]


# ── The log ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_log_replays_a_reply_then_follows_it_to_its_end(fake_redis_pool: ConnectionPool) -> None:
    log = TurnLog(fake_redis_pool)
    await log.append("t1", b"one")
    read: list[bytes] = []

    async def reader() -> None:
        async for data in log.read("t1", still_written=_true):
            read.append(data)

    reading = asyncio.create_task(reader())
    await asyncio.sleep(0.05)
    assert await log.append("t1", b"two") is False
    await log.end("t1")
    await asyncio.wait_for(reading, 2)
    assert read == [b"one", b"two"]


@pytest.mark.anyio
async def test_a_reader_lets_go_of_a_reply_whose_writer_went_away(
    fake_redis_pool: ConnectionPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(turn_log_module, "_BLOCK_MS", 20)
    log = TurnLog(fake_redis_pool)
    await log.append("t2", b"one")

    async def gone() -> bool:
        return False

    read = [data async for data in log.read("t2", still_written=gone)]
    assert read == [b"one"]


@pytest.mark.anyio
async def test_a_stop_is_seen_with_the_next_batch(fake_redis_pool: ConnectionPool) -> None:
    log = TurnLog(fake_redis_pool)
    assert await log.append("t3", b"") is False
    await log.request_stop("t3")
    assert await log.append("t3", b"more") is True


async def _true() -> bool:
    return True


# ── The sweeper ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_replies_left_running_by_a_gateway_that_went_away_are_interrupted(
    chat: uuid.UUID, db_session: AsyncSession,  # noqa: F811
) -> None:
    now = datetime.now(timezone.utc)
    left = ChatMessage(session_id=chat, role="assistant", content="half", status="running",
                       updated_at=now - timedelta(minutes=5))
    alive = ChatMessage(session_id=chat, role="assistant", content="", status="running", updated_at=now)
    db_session.add_all([left, alive])
    await db_session.flush()

    ended = await SessionDAO(db_session).interrupt_stale_turns(
        stale_before=now - timedelta(seconds=30), error=TURN_INTERRUPTED_ERROR,
    )
    await db_session.refresh(left)
    await db_session.refresh(alive)

    assert ended == 1
    assert (left.status, left.error, left.content) == ("interrupted", TURN_INTERRUPTED_ERROR, "half")
    assert alive.status == "running"
