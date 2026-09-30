"""A chat reply is a turn with a status, kept whatever becomes of the stream.

The reply's row is written as it starts (``running``) and ends ``complete``,
``failed`` or ``stopped``. A failure was never stored: the chat page guessed
one from a question left without an answer, and the error itself -- Qwen3.8's
template refusing the request, 2026-09-30 -- was gone once the page was left.
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

from faden_gateway.db.models.gateway import ChatMessage, ProviderType
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.services.gateway.service import GatewayService
from tests.test_gateway_pipeline_combined import ALIAS, _chunk, _Observability, _seed, _session, _token
from tests.test_gateway_stream_finish import ANSWER, _as_a_client_that_leaves, _model_streams

REFUSAL = "System message must be at the beginning."


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


async def _turns(db: AsyncSession, sid: uuid.UUID) -> list[ChatMessage]:
    """The session's messages as stored now: other sessions wrote them."""
    db.expire_all()
    return list((await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at),
    )).scalars().all())


def _model_says(monkeypatch: pytest.MonkeyPatch, *events: bytes, sent: list[dict[str, Any]] | None = None) -> None:
    """The model's server answers with *events*; what it was sent goes in *sent*."""

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        if sent is not None:
            sent.append(kwargs["payload"])

        async def gen() -> AsyncIterator[bytes]:
            for event in events:
                yield event

        return gen()

    monkeypatch.setattr(LLMAdapter, "completion", completion)


def _refused() -> bytes:
    return f"data: {json.dumps({'error': {'message': REFUSAL, 'code': 400}})}\n\n".encode()


async def _chat(client: AsyncClient, sid: uuid.UUID, text: str) -> Any:
    return await client.post("/v1/chat/completions", headers=_auth(), json={
        "model": ALIAS, "stream": True, "session_id": str(sid), "messages": [{"role": "user", "content": text}],
    })


async def _stored(db: AsyncSession, sid: uuid.UUID, *rows: tuple[str, str, str]) -> None:
    """Rows as a chat already holds them: (role, content, status), a second apart."""
    at = datetime.now(timezone.utc) - timedelta(minutes=5)
    for i, (role, content, status) in enumerate(rows):
        db.add(ChatMessage(
            session_id=sid, role=role, content=content, status=status, model_alias=ALIAS,
            error=REFUSAL if status == "failed" else None, created_at=at + timedelta(seconds=i),
        ))
    await db.commit()


@pytest.fixture
async def chat(fastapi_app: FastAPI, db_session: AsyncSession) -> uuid.UUID:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _session(db_session)
    await db_session.commit()
    return sid


# ── The reply's row ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_reply_is_written_as_it_starts_and_ends_complete(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _model_says(monkeypatch, _chunk("Hel"), _chunk("lo"), b"data: [DONE]\n\n")
    r = await _chat(client, chat, "hi")
    assert r.status_code == 200
    user, reply = await _turns(db_session, chat)
    assert (user.role, user.content) == ("user", "hi")
    assert (reply.role, reply.content, reply.status, reply.error) == ("assistant", "Hello", "complete", None)
    assert r.headers["x-faden-turn-id"] == str(reply.id), "the page knows which reply to try again"
    assert reply.updated_at is not None


@pytest.mark.anyio
async def test_a_refused_reply_is_kept_as_failed_with_its_error(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qwen3.8's template refused the request: the chat keeps why, after a reload too."""
    _model_says(monkeypatch, _refused(), b"data: [DONE]\n\n")
    await _chat(client, chat, "hi")
    reply = (await _turns(db_session, chat))[-1]
    assert (reply.status, reply.content, reply.error) == ("failed", "", REFUSAL)

    listed = await client.get(f"/v1/sessions/{chat}/messages", headers=_auth())
    last = listed.json()["data"][-1]
    assert (last["status"], last["error"]) == ("failed", REFUSAL)


@pytest.mark.anyio
async def test_a_reply_the_client_left_is_kept_as_stopped(
    fastapi_app: FastAPI, chat: uuid.UUID, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until replies run on their own, leaving ends them: what was written is kept, marked stopped."""
    _model_streams(monkeypatch)
    await _as_a_client_that_leaves(
        fastapi_app,
        {"model": ALIAS, "stream": True, "session_id": str(chat), "messages": [{"role": "user", "content": "hi"}]},
        leaves_after='"lant"',
        seen=[],
    )
    for _ in range(200):
        reply = (await _turns(db_session, chat))[-1]
        if reply.status != "running":
            break
        await asyncio.sleep(0.01)
    assert reply.status == "stopped"
    assert ANSWER.startswith(reply.content) and reply.content


@pytest.mark.anyio
async def test_a_failure_before_the_model_answers_is_kept_with_the_question(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The question is saved first; the chat shows it failed, not unanswered."""

    async def blocked(self: GatewayService, *_: Any, **__: Any) -> Any:
        raise GatewayError(status_code=403, message="Personal data is not allowed to leave.", code="pii_blocked")

    monkeypatch.setattr(GatewayService, "_scan_egress", blocked)
    r = await _chat(client, chat, "my name is Alice")
    assert r.status_code == 403
    user, reply = await _turns(db_session, chat)
    assert user.content == "my name is Alice"
    assert (reply.status, reply.error) == ("failed", "Personal data is not allowed to leave.")


# ── What the model is sent ─────────────────────────────────────────


@pytest.mark.anyio
async def test_failed_replies_are_not_sent_to_the_model(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stored(db_session, chat, ("user", "q1", "complete"), ("assistant", "half an ans", "failed"))
    sent: list[dict[str, Any]] = []
    _model_says(monkeypatch, _chunk("a2"), b"data: [DONE]\n\n", sent=sent)
    await _chat(client, chat, "q2")
    contents = [m.get("content") for m in sent[0]["messages"] if m["role"] != "system"]
    assert contents == ["q1", "q2"]


@pytest.mark.anyio
async def test_asking_again_replaces_the_failed_reply(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same question after a failed reply is trying again: no second question, no second failure."""
    await _stored(db_session, chat, ("user", "q1", "complete"), ("assistant", "", "failed"))
    _model_says(monkeypatch, _chunk("a1"), b"data: [DONE]\n\n")
    await _chat(client, chat, "q1")
    assert [(m.role, m.content, m.status) for m in await _turns(db_session, chat)] == [
        ("user", "q1", "complete"), ("assistant", "a1", "complete"),
    ]


# ── Trying again ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_last_failed_reply_is_tried_again_in_its_place(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stored(db_session, chat, ("user", "q1", "complete"), ("assistant", "", "failed"))
    failed = (await _turns(db_session, chat))[-1]
    sent: list[dict[str, Any]] = []
    _model_says(monkeypatch, _chunk("a1"), b"data: [DONE]\n\n", sent=sent)

    r = await client.post(f"/v1/sessions/{chat}/turns/{failed.id}/retry", headers=_auth(), json={})
    assert r.status_code == 200 and "a1" in r.text
    rows = await _turns(db_session, chat)
    assert [(m.role, m.content, m.status) for m in rows] == [("user", "q1", "complete"), ("assistant", "a1", "complete")]
    assert r.headers["x-faden-turn-id"] == str(rows[-1].id) != str(failed.id)
    assert sent[0]["model"] == ALIAS, "the failed reply's model, when none is named"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rows", "which"),
    [
        ((("user", "q1", "complete"), ("assistant", "a1", "complete")), -1),
        ((("user", "q1", "complete"), ("assistant", "", "failed"), ("user", "q2", "complete"),
          ("assistant", "a2", "complete")), 1),
    ],
    ids=["a-complete-reply", "not-the-last-reply"],
)
async def test_only_the_last_failed_or_stopped_reply_can_be_tried_again(
    rows: tuple[tuple[str, str, str], ...], which: int,
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stored(db_session, chat, *rows)
    turn = (await _turns(db_session, chat))[which]
    r = await client.post(f"/v1/sessions/{chat}/turns/{turn.id}/retry", headers=_auth(), json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "turn_not_retryable"


@pytest.mark.anyio
async def test_someone_else_s_chat_cannot_be_tried_again(
    chat: uuid.UUID, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _stored(db_session, chat, ("user", "q1", "complete"), ("assistant", "", "failed"))
    failed = (await _turns(db_session, chat))[-1]
    r = await client.post(f"/v1/sessions/{uuid.uuid4()}/turns/{failed.id}/retry", headers=_auth(), json={})
    assert r.status_code == 404
