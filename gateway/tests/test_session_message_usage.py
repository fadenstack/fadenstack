"""A stored reply brings the token usage its request logged.

The chat's token badge showed prompt + completion while a reply streamed
and, after a reload, only the reply's own size (``token_estimate``): 972
tokens became 2. The messages list now carries what the request used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import LLMGatewayRequestLog
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context

pytestmark = pytest.mark.anyio


def _log(trace_id: str, session_id: str, prompt: int, completion: int, at: datetime) -> LLMGatewayRequestLog:
    return LLMGatewayRequestLog(
        request_id=f"req-{trace_id}-{prompt}",
        trace_id=trace_id,
        tenant_id="t1",
        user_id="u1",
        endpoint="/v1/chat/completions",
        status_code=200,
        latency_ms=10,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        session_id=session_id,
        created_at=at,
    )


async def test_a_reply_brings_the_usage_its_request_logged(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    dao = SessionDAO(db_session)
    sess = await dao.create_session(tenant_id="t1", user_id="u1", title="usage")
    await dao.append_message(session_id=sess.id, role="user", content="Reply: pong")
    await dao.append_message(
        session_id=sess.id, role="assistant", content="pong", token_estimate=2, trace_id="tr-1",
    )
    await dao.append_message(
        session_id=sess.id, role="assistant", content="no log row", token_estimate=3, trace_id="tr-2",
    )
    db_session.add(_log("tr-1", str(sess.id), 970, 2, datetime.now(tz=UTC)))
    await db_session.flush()
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )

    resp = await client.get(f"/v1/sessions/{sess.id}/messages")

    assert resp.status_code == 200
    usage = {m["content"]: m["usage"] for m in resp.json()["data"]}
    assert usage == {
        "Reply: pong": None,
        "pong": {"prompt_tokens": 970, "completion_tokens": 2, "total_tokens": 972},
        "no log row": None,
    }


async def test_a_retried_request_uses_its_latest_row(db_session: AsyncSession) -> None:
    # The latest row per trace is Postgres's DISTINCT ON -- production's
    # database. This suite runs on SQLite, which renders a plain DISTINCT.
    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("DISTINCT ON needs PostgreSQL; this suite runs on SQLite")
    dao = SessionDAO(db_session)
    sess = await dao.create_session(tenant_id="t1", user_id="u1", title="retry")
    now = datetime.now(tz=UTC)
    db_session.add_all([
        _log("tr-9", str(sess.id), 5, 1, now - timedelta(seconds=30)),
        _log("tr-9", str(sess.id), 970, 2, now),
    ])
    await db_session.flush()

    usage = await dao.message_usage(session_id=sess.id, trace_ids=["tr-9"])

    assert usage == {"tr-9": {"prompt_tokens": 970, "completion_tokens": 2, "total_tokens": 972}}
