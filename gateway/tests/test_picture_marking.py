"""Generated pictures leave the gateway marked as made by AI, or not at all.

With the provenance module on, each picture goes to the engine's mark route
and leaves with the marked bytes, its receipt in ``X-Faden-Provenance``. A
picture the engine cannot mark fails the request with a 503 and no picture
of it; the module off, everything is as before.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, ProviderType
from faden_gateway.services.gateway import picture_marking
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.provenance_client import (
    MarkedImage,
    ProvenanceClient,
    ProvenanceMarkError,
)
from tests.test_agent_routes import (  # noqa: F401 - the fixture the agent route needs
    AGENT_ID,
    _headers,
    backend,
)
from tests.test_gateway_images import IMAGES, PICTURE, _Recording
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _token
from tests.test_provenance_module import BASE, TOKEN

MARK = b"|marked"
SECOND = base64.b64encode(base64.b64decode(PICTURE) + b"second").decode()
GIF = base64.b64encode(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;").decode()
LAYERS = [
    {"layer": "watermark", "method": "trustmark-Q", "applied": True},
    {"layer": "metadata", "method": "xmp", "applied": True},
    {"layer": "metadata", "method": "c2pa", "applied": True},
]
MARK_PATH = "/api/v1/provenance/images/mark"


def _marked(picture: str) -> str:
    """*picture* as the fake engine marks it."""
    return base64.b64encode(base64.b64decode(picture) + MARK).decode()


Answer = Callable[[httpx.Request], httpx.Response]


class _Engine:
    """The engine's mark route as the contract describes it; keeps what it was sent, fails as told."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.receipts: list[str] = []
        #: The answer to the n-th call (from 1) instead of a marked picture.
        self.fail: dict[int, Answer] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == MARK_PATH
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(403, json={"detail": "Forbidden"})
        body = json.loads(request.content)
        self.sent.append(body)
        self.requests.append(request)
        failure = self.fail.get(len(self.sent))
        if failure is not None:
            return failure(request)
        receipt = str(uuid.uuid4())
        self.receipts.append(receipt)
        return httpx.Response(200, json={
            "image": _marked(body["image"]), "media_type": body["media_type"], "receipt_id": receipt, "layers": LAYERS,
        })


def _answer(status: int, body: Any = None) -> Answer:
    return lambda request: httpx.Response(status, json=body)


def _raise(error: type[httpx.TransportError]) -> Answer:
    def answer(request: httpx.Request) -> httpx.Response:
        raise error("no answer", request=request)

    return answer


#: Every way a mark fails, and the code the caller gets.
FAILURES = [
    pytest.param(_answer(503, {"detail": {"code": "busy", "message": "The marking queue is full"}}), "busy", id="busy"),
    pytest.param(
        _answer(503, {"detail": {"code": "no_signing_key", "message": "No signing key"}}), "no_signing_key",
        id="no-signing-key",
    ),
    pytest.param(_answer(503, {"detail": {"code": "unavailable", "message": "down"}}), "unavailable", id="unavailable"),
    pytest.param(_answer(500), "unavailable", id="server-error"),
    pytest.param(_answer(401, {"detail": "Not authenticated"}), "token_refused", id="no-token"),
    pytest.param(_answer(403, {"detail": "Forbidden"}), "token_refused", id="wrong-token"),
    pytest.param(_answer(413), "too_large", id="too-large"),
    pytest.param(_answer(422, {"detail": "not a picture"}), "not_a_picture", id="not-a-picture"),
    pytest.param(_raise(httpx.ConnectError), "unreachable", id="unreachable"),
    pytest.param(_raise(httpx.ReadTimeout), "timeout", id="timeout"),
    pytest.param(_answer(200, {"image": "not base64!", "media_type": "image/png"}), "unreadable_answer", id="unreadable"),
    pytest.param(
        _answer(200, {"image": PICTURE, "media_type": "image/png", "receipt_id": str(uuid.uuid4()),
                      "layers": [{"layer": "watermark", "method": "trustmark-Q", "applied": False}]}),
        "not_marked", id="not-marked",
    ),
]


@pytest.fixture
async def engine(fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Engine]:
    """The module on: the app's provenance client marks with a fake engine."""
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        monkeypatch.setattr(fastapi_app.state, "provenance_client", client, raising=False)
        yield fake


def _paints(monkeypatch: pytest.MonkeyPatch, *data: dict[str, Any]) -> list[dict[str, Any]]:
    """The image server answers *data*; with none, ``PICTURE`` for each picture asked for."""
    calls: list[dict[str, Any]] = []

    async def image_generation(self: LLMAdapter, **kwargs: Any) -> CompletionResult:
        calls.append(kwargs)
        pictures = list(data) or [{"b64_json": PICTURE}] * kwargs["payload"].get("n", 1)
        return CompletionResult(status_code=200, payload={"created": 1, "data": pictures})

    monkeypatch.setattr(LLMAdapter, "image_generation", image_generation)
    return calls


async def _images(client: AsyncClient, **body: Any) -> httpx.Response:
    body.setdefault("prompt", "A servo motor on a packaging line, clean product photo")
    return await client.post("/v1/images/generations", headers={"Authorization": f"Bearer {_token()}"}, json=body)


async def _seed_images(fastapi_app: FastAPI, db_session: AsyncSession) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=IMAGES)
    fastapi_app.state.gateway_observability = _Recording()


def _refused(r: httpx.Response, code: str) -> None:
    """The 503 a picture that could not be marked gives: no picture, no receipt."""
    assert r.status_code == 503, r.text
    error = r.json()["error"]
    assert error["type"] == "provenance_unavailable"
    assert error["code"] == code
    assert "could not be marked as AI-generated" in error["message"]
    assert "data" not in r.json()
    assert PICTURE not in r.text
    assert picture_marking.HEADER not in r.headers


# ── The client ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_client_sends_the_picture_and_reads_the_marked_one() -> None:
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        marked = await client.mark_image(
            base64.b64decode(PICTURE), media_type="image/png", model="z-image-turbo", agent="agent-7",
        )

    assert marked == MarkedImage(
        image=base64.b64decode(PICTURE) + MARK, media_type="image/png", receipt_id=fake.receipts[0],
        layers=tuple(LAYERS),
    )
    assert fake.requests[0].url == httpx.URL(f"{BASE}{MARK_PATH}")
    assert fake.requests[0].extensions["timeout"]["read"] == 40.0
    assert fake.sent == [{
        "image": PICTURE, "media_type": "image/png", "model": "z-image-turbo", "agent": "agent-7",
        "edit_kind": "generated",
    }]


@pytest.mark.anyio
async def test_the_client_sends_an_edit_as_rewritten() -> None:
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        await client.mark_image(base64.b64decode(PICTURE), media_type="image/png", model="m", edit_kind="rewritten")
    assert fake.sent[0]["edit_kind"] == "rewritten"
    assert fake.sent[0]["agent"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "code"), FAILURES)
async def test_the_client_raises_the_reason_a_picture_was_not_marked(failure: Answer, code: str) -> None:
    fake = _Engine()
    fake.fail[1] = failure
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        with pytest.raises(ProvenanceMarkError) as failed:
            await client.mark_image(base64.b64decode(PICTURE), media_type="image/png", model="m")
    assert failed.value.code == code


@pytest.mark.anyio
async def test_the_client_says_an_empty_token_is_why_it_was_refused() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_answer(401, {"detail": "Not authenticated"}))) as http:
        with pytest.raises(ProvenanceMarkError) as failed:
            await ProvenanceClient(base_url=BASE, http_client=http, service_token="").mark_image(
                base64.b64decode(PICTURE), media_type="image/png", model="m",
            )
    assert failed.value.code == "token_refused"
    assert "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN is empty" in str(failed.value)


# ── /v1/images/generations ────────────────────────────────────────


@pytest.mark.anyio
async def test_each_picture_leaves_marked_with_its_receipt_in_the_header(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed_images(fastapi_app, db_session)
    _paints(monkeypatch, {"b64_json": PICTURE, "revised_prompt": "a servo"}, {"b64_json": SECOND})

    r = await _images(client, model=ALIAS, n=2)

    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"b64_json": _marked(PICTURE), "revised_prompt": "a servo"}, {"b64_json": _marked(SECOND)}]
    assert r.headers[picture_marking.HEADER] == ",".join(engine.receipts)
    assert len(engine.receipts) == 2
    assert [(s["media_type"], s["model"], s["agent"], s["edit_kind"]) for s in engine.sent] == [
        ("image/png", ALIAS, None, "generated"),
    ] * 2
    assert [s["image"] for s in engine.sent] == [PICTURE, SECOND]


@pytest.mark.anyio
async def test_with_the_module_off_the_pictures_leave_as_made(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_images(fastapi_app, db_session)
    monkeypatch.setattr(fastapi_app.state, "provenance_client", None, raising=False)
    _paints(monkeypatch, {"b64_json": PICTURE}, {"url": "https://images.example/1.png"})

    r = await _images(client, model=ALIAS, n=2)

    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"b64_json": PICTURE}, {"url": "https://images.example/1.png"}]
    assert picture_marking.HEADER not in r.headers


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "code"), FAILURES)
async def test_a_picture_that_cannot_be_marked_fails_the_request(
    failure: Answer, code: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine, caplog: pytest.LogCaptureFixture,
) -> None:
    await _seed_images(fastapi_app, db_session)
    _paints(monkeypatch)
    engine.fail[1] = failure

    with caplog.at_level(logging.WARNING, logger=picture_marking.__name__):
        r = await _images(client, model=ALIAS)

    _refused(r, code)
    lines = [rec for rec in caplog.records if rec.name == picture_marking.__name__]
    assert len(lines) == 1, "one line with the reason"
    assert f"({code})" in lines[0].getMessage()
    assert PICTURE not in caplog.text, "never the picture"
    row = (await db_session.execute(select(LLMGatewayRequestLog))).scalar_one()
    assert (row.endpoint, row.status_code, row.error_code) == ("/v1/images/generations", 503, code)


@pytest.mark.anyio
async def test_one_picture_that_cannot_be_marked_fails_them_all(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed_images(fastapi_app, db_session)
    _paints(monkeypatch, {"b64_json": PICTURE}, {"b64_json": SECOND}, {"b64_json": PICTURE})
    engine.fail[2] = _answer(503, {"detail": {"code": "busy", "message": "The marking queue is full"}})

    r = await _images(client, model=ALIAS, n=3)

    _refused(r, "busy")
    assert _marked(PICTURE) not in r.text and SECOND not in r.text
    assert len(engine.sent) == 2, "marking stops at the first failure"


@pytest.mark.anyio
async def test_a_picture_given_as_a_link_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed_images(fastapi_app, db_session)
    _paints(monkeypatch, {"b64_json": PICTURE}, {"url": "https://images.example/1.png"})

    r = await _images(client, model=ALIAS, n=2)

    _refused(r, "picture_as_link")
    assert "images.example" not in r.text


@pytest.mark.anyio
async def test_a_picture_the_engine_does_not_mark_is_refused_without_asking_it(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed_images(fastapi_app, db_session)
    _paints(monkeypatch, {"b64_json": GIF})

    r = await _images(client, model=ALIAS)

    _refused(r, "unsupported_media_type")
    assert engine.sent == []


@pytest.mark.anyio
async def test_an_agents_picture_names_the_agent(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=IMAGES)
    _paints(monkeypatch)

    r = await client.post(
        "/v1/agents/eplan-assistant/images/generations", headers=_headers(), json={"prompt": "A slide's picture"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{"b64_json": _marked(PICTURE)}]
    assert r.headers[picture_marking.HEADER] == engine.receipts[0]
    assert engine.sent[0]["agent"] == AGENT_ID


@pytest.mark.anyio
async def test_an_edit_is_marked_as_rewritten() -> None:
    """No edit route yet; the marking of an images answer takes the kind of picture it is."""
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        payload, receipts = await picture_marking.mark_images(
            client, {"created": 1, "data": [{"b64_json": PICTURE}]},
            model="m", agent=None, request_id="r-1", edit_kind="rewritten",
        )
    assert fake.sent[0]["edit_kind"] == "rewritten"
    assert payload["data"] == [{"b64_json": _marked(PICTURE)}]
    assert receipts == fake.receipts


@pytest.mark.anyio
async def test_marking_raises_the_503_the_caller_gets() -> None:
    fake = _Engine()
    fake.fail[1] = _raise(httpx.ConnectError)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        with pytest.raises(GatewayError) as failed:
            await picture_marking.mark_images(
                client, {"data": [{"b64_json": PICTURE}]}, model="m", agent=None, request_id="r-1",
            )
    assert (failed.value.status_code, failed.value.error_type, failed.value.code) == (
        503, "provenance_unavailable", "unreachable",
    )


# ── A chat whose model answers with pictures ─────────────────────


def _chat_answers(monkeypatch: pytest.MonkeyPatch, message: dict[str, Any]) -> None:
    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        return CompletionResult(status_code=200, payload={
            "id": "c", "object": "chat.completion", "model": ALIAS,
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)


async def _chat(client: AsyncClient) -> httpx.Response:
    return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "Draw a lighthouse"}],
    })


@pytest.mark.anyio
async def test_a_chats_pictures_leave_marked(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _chat_answers(monkeypatch, {"content": "Here it is.", "images": [
        {"type": "image_url", "index": 0, "image_url": {"url": f"data:image/png;base64,{PICTURE}"}},
    ]})

    r = await _chat(client)

    assert r.status_code == 200, r.text
    message = r.json()["choices"][0]["message"]
    assert message["content"] == "Here it is."
    assert message["images"] == [
        {"type": "image_url", "index": 0, "image_url": {"url": f"data:image/png;base64,{_marked(PICTURE)}"}},
    ]
    assert r.headers[picture_marking.HEADER] == engine.receipts[0]
    assert engine.sent[0]["edit_kind"] == "generated"


@pytest.mark.anyio
async def test_a_chat_without_pictures_is_not_sent_to_the_engine(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _chat_answers(monkeypatch, {"content": "A lighthouse stands on a rock."})

    r = await _chat(client)

    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "A lighthouse stands on a rock."
    assert picture_marking.HEADER not in r.headers
    assert engine.sent == []


@pytest.mark.anyio
async def test_a_chats_picture_that_cannot_be_marked_fails_the_answer(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _chat_answers(monkeypatch, {"content": "Here it is.", "images": [
        {"type": "image_url", "index": 0, "image_url": {"url": f"data:image/png;base64,{PICTURE}"}},
    ]})
    engine.fail[1] = _answer(503, {"detail": {"code": "no_signing_key", "message": "No signing key"}})

    r = await _chat(client)

    _refused(r, "no_signing_key")
    assert "Here it is." not in r.text
