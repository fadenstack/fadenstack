"""A resumed stream carries its trace id, as the stream it resumes did.

A chat reloaded mid-reply reconnects to the buffered stream; without the
trace id that reply's info button did nothing until another reload.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatSession
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.stream_buffer import StreamBuffer

pytestmark = pytest.mark.anyio


async def _chat_of_u1(fastapi_app: FastAPI, db: AsyncSession) -> str:
    """A chat of the user the requests come from: a reply is read only by its own user."""
    sess = ChatSession(tenant_id="t1", user_id="u1", title="t")
    db.add(sess)
    await db.flush()
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )
    return str(sess.id)


async def test_the_resumed_stream_sends_the_trace_id(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    sid = await _chat_of_u1(fastapi_app, db_session)
    buf = StreamBuffer()
    buf.start(sid, trace_id="tr-9")
    buf.push(sid, b'data: {"choices":[{"delta":{"content":"po"}}]}\n\n')
    buf.finish(sid)
    fastapi_app.state.stream_buffer = buf

    resp = await client.get(f"/v1/sessions/{sid}/stream")

    assert resp.status_code == 200
    assert resp.headers["x-langfuse-trace-id"] == "tr-9"
    assert b'"po"' in resp.content


async def test_a_stream_started_without_one_sends_none(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    sid = await _chat_of_u1(fastapi_app, db_session)
    buf = StreamBuffer()
    buf.start(sid)
    buf.finish(sid)
    fastapi_app.state.stream_buffer = buf

    resp = await client.get(f"/v1/sessions/{sid}/stream")

    assert "x-langfuse-trace-id" not in resp.headers


async def test_someone_else_s_stream_is_not_read(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    sid = await _chat_of_u1(fastapi_app, db_session)
    buf = StreamBuffer()
    buf.start(sid, trace_id="tr-9")
    buf.finish(sid)
    fastapi_app.state.stream_buffer = buf
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="someone-else", tenant_id="t1", raw_claims={},
    )

    assert (await client.get(f"/v1/sessions/{sid}/stream")).status_code == 404
    assert (await client.get(f"/v1/sessions/{sid}/stream/status")).status_code == 404
