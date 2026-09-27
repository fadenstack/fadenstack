"""What a streamed answer owes after its last token, whatever the client does.

Once the model has finished, the reply is saved in the chat session and the
request is logged for usage and cost. That work ran in the response's own task,
after the closing ``[DONE]`` had gone out, and Starlette cancels that task when
the client disconnects. A client that stops reading at ``[DONE]`` and closes --
the OpenAI SDKs do -- lost both every time: on 2026-09-25 a replay of the chat
page's own requests saved none of 50 replies and logged none of the 50 requests,
and the chat page itself lost 14 of 389 replies.

``httpx``'s ASGI transport waits for the application to finish before it returns
anything, so it cannot leave early; these tests drive the application directly,
as uvicorn does (ASGI spec 2.3, where Starlette listens for the disconnect).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, ProviderType
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.service import _BACKGROUND, GatewayService, _split_done
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _chunk,
    _history,
    _Observability,
    _seed,
    _session,
    _token,
)

ANSWER = "The plan covers spindles and coolant."


def _model_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model answers ANSWER four characters at a time.

    Between pieces it gives the loop a turn, as a network read does: that is
    where a disconnect lands.
    """

    async def completion(self: LLMAdapter, **_: Any) -> Any:
        async def pieces() -> Any:
            for i in range(0, len(ANSWER), 4):
                await asyncio.sleep(0)
                yield _chunk(ANSWER[i:i + 4])
            await asyncio.sleep(0)
            yield b"data: [DONE]\n\n"
        return pieces()

    monkeypatch.setattr(LLMAdapter, "completion", completion)


async def _as_a_client_that_leaves(
    app: FastAPI, body: dict[str, Any], *, leaves_after: str, seen: list[str],
) -> None:
    """One streamed request from a client that disconnects once an event contains *leaves_after*.

    Every event the client reads is appended to *seen* as ``read:<event>``.
    """
    raw = json.dumps(body).encode()
    asked = False
    gone = asyncio.Event()
    pending = b""

    async def receive() -> dict[str, Any]:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal pending
        if message["type"] != "http.response.body" or gone.is_set():
            return
        pending += message.get("body", b"")
        while b"\n\n" in pending:
            event, pending = pending.split(b"\n\n", 1)
            seen.append(f"read:{event.decode()}")
            if leaves_after in event.decode():
                gone.set()
                return

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
            (b"authorization", f"Bearer {_token()}".encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await app(scope, receive, send)


def _record_the_saving(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    """Note in *seen* when the reply has been saved and when the request has been logged."""
    save = GatewayService._persist_stream_assistant_response  # noqa: SLF001
    log = GatewayService._audit_on_own_session  # noqa: SLF001

    async def saved(self: GatewayService, **kwargs: Any) -> None:
        await save(self, **kwargs)
        seen.append("saved")

    async def logged(self: GatewayService, **kwargs: Any) -> None:
        await log(self, **kwargs)
        seen.append("logged")

    monkeypatch.setattr(GatewayService, "_persist_stream_assistant_response", saved)
    monkeypatch.setattr(GatewayService, "_audit_on_own_session", logged)


async def _logged(db: AsyncSession, sid: uuid.UUID) -> int:
    return (await db.execute(
        select(func.count()).select_from(LLMGatewayRequestLog).where(LLMGatewayRequestLog.session_id == str(sid)),
    )).scalar_one()


async def _until(condition: Any, seconds: float = 2.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_a_client_that_closes_at_done_finds_the_reply_saved_and_the_request_logged(
    fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[DONE]`` is sent only once the reply is saved; the request is logged even though the client has left."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _model_streams(monkeypatch)
    sid = await _session(db_session)
    seen: list[str] = []
    _record_the_saving(monkeypatch, seen)

    await _as_a_client_that_leaves(
        fastapi_app,
        {"model": ALIAS, "stream": True, "session_id": str(sid), "messages": [{"role": "user", "content": "hi"}]},
        leaves_after="[DONE]",
        seen=seen,
    )

    assert seen.index("saved") < seen.index("read:data: [DONE]"), "a reload at [DONE] finds the reply"
    assert await _history(db_session, sid) == [("user", "hi"), ("assistant", ANSWER)]
    await _until(lambda: "logged" in seen)
    assert await _logged(db_session, sid) == 1


@pytest.mark.anyio
async def test_a_client_that_leaves_before_the_end_does_not_stop_the_saving(
    fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tab closed on the last words: the saving goes on without anyone waiting for it."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _model_streams(monkeypatch)
    sid = await _session(db_session)
    seen: list[str] = []
    _record_the_saving(monkeypatch, seen)

    await _as_a_client_that_leaves(
        fastapi_app,
        {"model": ALIAS, "stream": True, "session_id": str(sid), "messages": [{"role": "user", "content": "hi"}]},
        leaves_after='"lant"',
        seen=seen,
    )
    await _until(lambda: "logged" in seen)

    history = await _history(db_session, sid)
    assert [role for role, _ in history] == ["user", "assistant"]
    assert ANSWER.startswith(history[1][1]) and "coolant" in history[1][1], "what had streamed is kept"
    assert await _logged(db_session, sid) == 1
    assert all(task.done() for task in _BACKGROUND), "nothing left running"


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_the_model_is_called_with_no_transaction_holding_a_connection(
    stream: bool, fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request's session used to stay in its transaction through the model call.

    Each concurrent answer then pinned one pooled connection, "idle in
    transaction", until the response was over: at 64 streams the gateway held 59
    and Postgres refused every other service.
    """
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _session(db_session)
    open_at_call: list[bool] = []

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        open_at_call.append(db_session.in_transaction())
        if kwargs.get("stream"):
            async def pieces() -> Any:
                yield _chunk(ANSWER)
                yield b"data: [DONE]\n\n"
            return pieces()
        return CompletionResult(status_code=200, payload={
            "id": "c", "object": "chat.completion", "model": ALIAS,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=5.0) as client:
        r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
            "model": ALIAS, "stream": stream, "session_id": str(sid), "messages": [{"role": "user", "content": "hi"}],
        })
    assert r.status_code == 200
    assert open_at_call == [False]


@pytest.mark.parametrize(
    ("chunk", "before", "closing"),
    [
        (b'data: {"a": 1}\n\n', b'data: {"a": 1}\n\n', False),
        (b"data: [DONE]\n\n", b"", True),
        (b'data: {"a": 1}\n\ndata: [DONE]\n\n', b'data: {"a": 1}\n\n', True),
        (b"data:[DONE]\n\n", b"", True),
    ],
    ids=["content", "done-alone", "done-shares-the-chunk", "done-without-space"],
)
def test_the_closing_done_is_split_off_its_chunk(chunk: bytes, before: bytes, closing: bool) -> None:
    assert _split_done(chunk) == (before, closing)
