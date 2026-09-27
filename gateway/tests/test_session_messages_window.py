"""A chat reopens on its latest messages, not its first.

The messages list took the first ``limit`` messages: a chat past 100
messages reopened at its beginning with its newest turns missing. It now
returns the newest ``limit``, oldest first. The summarizer's list_messages
keeps its order (the first messages after a point).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dao.session_dao import SessionDAO
from llm_port_api.db.models.gateway import ChatMessage
from llm_port_api.services.gateway.auth import AuthContext, get_auth_context

pytestmark = pytest.mark.anyio


async def test_the_messages_list_is_the_latest_window_in_order(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    dao = SessionDAO(db_session)
    sess = await dao.create_session(tenant_id="t1", user_id="u1", title="long")
    start = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    db_session.add_all([
        ChatMessage(
            session_id=sess.id,
            role="user" if i % 2 else "assistant",
            content=f"m{i}",
            created_at=start + timedelta(seconds=i),
        )
        for i in range(1, 106)
    ])
    await db_session.flush()
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )

    resp = await client.get(f"/v1/sessions/{sess.id}/messages", params={"limit": 100})

    contents = [m["content"] for m in resp.json()["data"]]
    assert len(contents) == 100
    assert (contents[0], contents[-1]) == ("m6", "m105")
    # The summarizer's order is untouched: the first messages.
    first = await dao.list_messages(session_id=sess.id, limit=3)
    assert [m.content for m in first] == ["m1", "m2", "m3"]
