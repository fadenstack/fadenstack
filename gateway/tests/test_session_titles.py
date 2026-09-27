"""A session created without a title is named from its first message.

The chat page names the sessions it creates; one created through the API,
the SDK or a script had no title and stayed "Untitled chat" for good. And a
title -- the start of the first message -- is stored encrypted, like it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db import crypto
from faden_gateway.db.models.gateway import ChatSession, ProviderType
from faden_gateway.services.gateway.session_titles import TITLE_MAX_CHARS, title_from_message
from tests.test_gateway_pipeline_combined import ALIAS, _model_answers, _Observability, _seed, _token


def test_a_title_is_the_start_of_the_message_on_one_line() -> None:
    assert title_from_message("  How do I\n  deploy   Qwen? ") == "How do I deploy Qwen?"
    assert title_from_message(" \n ") is None
    long = "word " * 40
    title = title_from_message(long)
    assert title is not None and len(title) <= TITLE_MAX_CHARS and title.endswith("…")
    assert not title[:-1].endswith(" ")


async def _ask(client: AsyncClient, sid: str, question: str) -> None:
    resp = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": sid, "messages": [{"role": "user", "content": question}],
    })
    assert resp.status_code == 200, resp.text


@pytest.mark.anyio
async def test_an_untitled_session_is_named_by_its_first_message_and_kept(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _model_answers(monkeypatch, "ok", [])
    untitled = ChatSession(tenant_id="tenant-a", user_id="user-1", title=None)
    titled = ChatSession(tenant_id="tenant-a", user_id="user-1", title="Launch plan")
    db_session.add_all([untitled, titled])
    await db_session.flush()

    await _ask(client, str(untitled.id), "When is the launch of the new model?")
    await _ask(client, str(untitled.id), "And the second phase?")
    await _ask(client, str(titled.id), "When is the launch?")

    await db_session.refresh(untitled)
    await db_session.refresh(titled)
    assert untitled.title == "When is the launch of the new model?"  # the first message, not the second
    assert titled.title == "Launch plan"  # a title given is kept


@pytest.mark.anyio
async def test_a_title_is_encrypted_at_rest(db_session: AsyncSession) -> None:
    crypto.configure("test-master-key-for-chat-titles-0123456789")
    try:
        sess = ChatSession(tenant_id="tenant-a", user_id="user-1", title="My salary review with Alice")
        db_session.add(sess)
        await db_session.flush()

        stored = (await db_session.execute(
            text("SELECT title FROM chat_session WHERE id = :id"), {"id": sess.id.hex},
        )).scalar_one_or_none()
        if stored is None:  # the id column's text form differs by dialect
            stored = (await db_session.execute(
                text("SELECT title FROM chat_session WHERE id = :id"), {"id": str(sess.id)},
            )).scalar_one()
        assert stored.startswith("gAAAAA")  # Fernet ciphertext
        assert "Alice" not in stored

        db_session.expire(sess)
        await db_session.refresh(sess)
        assert sess.title == "My salary review with Alice"
    finally:
        crypto.set_key_provider(None)  # type: ignore[arg-type]
