"""Every model answer with text leaves with a signed receipt; the provenance engine's public routes.

With the provenance module on, a whole answer carries its text's receipt in ``X-Faden-Provenance``, and
a streamed one ends with ``faden.provenance`` before ``[DONE]`` for a client that reads the gateway's
events. The engine gets the hash of the text the client got, never the text, and the watermark status
per the engine's table. An engine that fails never holds an answer back or fails it; an answer of tool
calls only gets no receipt; with the module off nothing changes. The agent routes for the SDK's
marking -- policy, records, placements -- authenticate as the other agent routes; the anchors need no
sign-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import picture_marking, text_receipts
from faden_gateway.services.gateway import service as service_module
from faden_gateway.services.gateway.content_hash import text_sha256
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.provenance_client import (
    ProvenanceClient,
    ProvenanceRequestError,
    SignedReceipt,
)
from faden_gateway.services.registry import service_registry
from tests.test_agent_routes import (  # noqa: F401 - the fixture the agent routes need
    AGENT_ID,
    MAINTAINER,
    OTHER_AGENT_ID,
    USER,
    _agent_token,
    _headers,
    _login_token,
    _profile,
    backend,
)
from tests.test_chat_turns_background import _ended, _replies
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _install_pii,
    _Observability,
    _pii,
    _seed,
    _session,
    _token,
)
from tests.test_provenance_module import BASE, TOKEN

RECEIPTS = "/api/v1/provenance/receipts"
RECORDS = "/api/v1/provenance/records/sign"
PLACEMENTS = "/api/v1/provenance/placements"
ANCHORS_PATH = "/api/v1/provenance/anchors"
POLICY_PATH = "/api/v1/provenance/policy"
VERIFY_PATH = "/api/v1/provenance/verify"
VERIFIED = {
    "contract": "provenance/v1", "verdict": "ai-generated", "checked_at": "2026-10-18T10:00:00Z",
    "content_hash": "2" * 64, "detector": "fadenstack-provenance/0.4.5", "signature": None,
    "findings": [{"layer": "watermark", "method": "vllm-gumbel", "found": True, "detail": "x", "score": 1e-9}],
}

ANSWER = "Page 12 holds the motor circuit."
#: An answer long enough to carry a watermark (with usage saying 250 completion tokens).
LONG = " ".join(["The pump P-200 is due for a seal check next week."] * 30)
EVENTS = {"X-Faden-Events": "ag-ui"}
TOOL_CALL = {"id": "call_1", "type": "function", "function": {"name": "read_page", "arguments": "{\"page\": 12}"}}
READ_PAGE = {"type": "function", "function": {"name": "read_page", "parameters": {"type": "object"}}}

POLICY = {
    "exempt_edit_kinds": [], "internal_technical": False, "on_missing": "record", "output_log": False,
    "short_text_tokens": 200, "visible_label": False,
}
ANCHORS = {
    "contract": "provenance/v1",
    "signing_chains": ["-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"],
    "trust_anchors": ["-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----\n"],
    "detector": {"id": "faden-detector/0.4.5", "methods": ["c2pa", "trustmark", "receipt", "record"]},
}
RECORD = {"content_sha256": "a" * 64, "media_type": "text/plain", "edit_kind": "generated", "receipt_id": "rcpt-1"}
PLACEMENT = {
    "record_id": "rec-1", "tool": "insert_text", "sink_kind": "word.range", "media_type": "text/plain",
    "edit_kind": "generated", "length_class": "short",
    "layers": [{"layer": "metadata", "method": "custom-xml", "applied": True},
               {"layer": "watermark", "method": "vllm-gumbel", "applied": False, "reason": "short-text"}],
}

Answer = Callable[[httpx.Request], httpx.Response]


def signed_receipt(body: dict[str, Any], receipt_id: str) -> dict[str, Any]:
    """The engine's ``SignedReceipt`` for a ``ReceiptRequest``."""
    return {
        "receipt": {
            "contract": "provenance/v1", "id": receipt_id, "kind": body["kind"],
            "model": {"id": body["model"], "version": body.get("model_version")},
            "created_at": "2026-10-18T09:30:00Z", "watermark": body["watermark"],
            "output_hash": "0" * 64, "hash_key_version": 1,
        },
        "signature": f"eyJhbGciOiJFUzI1NiJ9.{receipt_id}.c2lnbmF0dXJl",
        "timestamp": None,
    }


class _Engine:
    """The engine's fast lane as the contract describes it: receipts, records, placements, policy, anchors."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.receipts: list[str] = []
        #: The answer to every call instead of the contract's, when set.
        self.fail: Answer | None = None
        #: Called as a receipt is asked for.
        self.on_receipt: Callable[[], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(403, json={"detail": "Forbidden"})
        path = request.url.path
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        if body is not None:
            self.bodies[path].append(body)
        if self.fail is not None:
            return self.fail(request)
        if path == RECEIPTS:
            return self._receipt(body or {})
        if path == RECORDS:
            return self._record(body or {})
        fixed = {
            PLACEMENTS: (202, {"stored": True}), ANCHORS_PATH: (200, ANCHORS), POLICY_PATH: (200, POLICY),
            VERIFY_PATH: (200, VERIFIED),
        }
        status, answer = fixed.get(path, (404, None))
        return httpx.Response(status, json=answer)

    def _receipt(self, body: dict[str, Any]) -> httpx.Response:
        if self.on_receipt is not None:
            self.on_receipt()
        receipt_id = f"rcpt-{uuid.uuid4().hex[:12]}"
        self.receipts.append(receipt_id)
        return httpx.Response(200, json=signed_receipt(body, receipt_id))

    @staticmethod
    def _record(body: dict[str, Any]) -> httpx.Response:
        record = {
            "contract": "provenance/v1", "id": "rec-1", "digital_source_type": "trainedAlgorithmicMedia",
            "edit_kind": body["edit_kind"], "system": {"name": "Fadenstack", "version": "0.4.5"},
            "provider": "Example GmbH", "created_at": "2026-10-18T09:31:00Z", "media_type": body["media_type"],
            "content_hash": "1" * 64, "hash_key_version": 1, "receipt_id": body.get("receipt_id"),
        }
        return httpx.Response(200, json={"record": record, "signature": "eyJ.rec-1.sig", "timestamp": None})

    @property
    def asked(self) -> list[dict[str, Any]]:
        """The receipt requests, in their order."""
        return self.bodies[RECEIPTS]


def _answer(status: int, body: Any = None) -> Answer:
    return lambda request: httpx.Response(status, json=body)


def _raise(error: type[Exception]) -> Answer:
    def answer(request: httpx.Request) -> httpx.Response:
        if issubclass(error, httpx.TransportError):
            raise error("no answer", request=request)
        raise error("boom")

    return answer


#: The ways the engine fails a call, and the code the gateway gives them.
FAILURES = [
    pytest.param(_answer(503, {"detail": {"code": "busy", "message": "Too many at once"}}), "busy", id="busy"),
    pytest.param(
        _answer(503, {"detail": {"code": "no_signing_key", "message": "No signing key"}}), "no_signing_key",
        id="no-signing-key",
    ),
    pytest.param(_answer(500), "unavailable", id="server-error"),
    pytest.param(_answer(401, {"detail": "Not authenticated"}), "token_refused", id="no-token"),
    pytest.param(_answer(403, {"detail": "Forbidden"}), "token_refused", id="wrong-token"),
    pytest.param(_raise(httpx.ConnectError), "unreachable", id="unreachable"),
    pytest.param(_raise(httpx.ReadTimeout), "timeout", id="timeout"),
]


async def _settled() -> None:
    """The gateway's background work done: a receipt nobody waits for, a chat page's reply."""
    running = list(service_module._BACKGROUND)  # noqa: SLF001
    if running:
        await asyncio.wait(running, timeout=5)


@pytest.fixture(autouse=True)
async def _work_ends_before_the_database(db_session: AsyncSession) -> AsyncIterator[None]:
    yield
    await _settled()


@pytest.fixture
async def engine(fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Engine]:
    """The module on: the app's provenance client asks a fake engine."""
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        monkeypatch.setattr(fastapi_app.state, "provenance_client", client, raising=False)
        yield fake
        await _settled()


@pytest.fixture
def pii(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _install_pii(monkeypatch)
    yield
    service_registry.configure("pii", enabled=False, url=None)


def _event(body: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({'id': 'c', 'object': 'chat.completion.chunk', 'model': ALIAS, **body})}\n\n".encode()


def _answers(
    monkeypatch: pytest.MonkeyPatch,
    text: str = ANSWER,
    *,
    completion_tokens: int | None = 7,
    tool_calls: list[dict[str, Any]] | None = None,
    content: Any = None,
    state: dict[str, Any] | None = None,
    fail_after: int | None = None,
) -> list[dict[str, Any]]:
    """The model answers *text* -- whole, or in pieces with usage last -- and keeps what it was sent.

    *content* is a whole answer's ``content`` instead of *text*; *fail_after* breaks the stream after
    that many pieces; ``state["last_token"]`` is set once the last token has gone.
    """
    sent: list[dict[str, Any]] = []
    usage = (
        {"prompt_tokens": 9, "completion_tokens": completion_tokens, "total_tokens": 9 + completion_tokens}
        if completion_tokens is not None else None
    )
    finish = "tool_calls" if tool_calls else "stop"

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        if kwargs.get("stream"):
            async def pieces() -> AsyncIterator[bytes]:
                for n, i in enumerate(range(0, len(text), 5)):
                    if fail_after is not None and n == fail_after:
                        raise ConnectionError("upstream reset")
                    yield _event({"choices": [{"index": 0, "delta": {"content": text[i:i + 5]}, "finish_reason": None}]})
                if tool_calls:
                    deltas = [{"index": n, **call} for n, call in enumerate(tool_calls)]
                    yield _event({"choices": [{"index": 0, "delta": {"tool_calls": deltas}, "finish_reason": None}]})
                yield _event({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
                if usage is not None:
                    yield _event({"choices": [], "usage": usage})
                if state is not None:
                    state["last_token"] = True
                yield b"data: [DONE]\n\n"

            return pieces()
        message: dict[str, Any] = {"role": "assistant", "content": content if content is not None else (text or None)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return CompletionResult(status_code=200, payload={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": ALIAS,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            **({"usage": usage} if usage is not None else {}),
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)
    return sent


async def _seeded(
    fastapi_app: FastAPI, db_session: AsyncSession, provider: ProviderType = ProviderType.VLLM,
    node_metadata: dict[str, Any] | None = None, pii: dict[str, Any] | None = None,
) -> None:
    await _seed(db_session, provider=provider, pii=pii, node_metadata=node_metadata)
    fastapi_app.state.gateway_observability = _Observability()


def _auth(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", **extra}


async def _ask(client: AsyncClient, *, stream: bool = False, events: bool = False, **body: Any) -> httpx.Response:
    return await client.post("/v1/chat/completions", headers=_auth(**(EVENTS if events else {})), json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "What is on page 12?"}], **body,
    })


def _sse(text: str) -> list[tuple[str | None, Any]]:
    """An SSE body's events: (name, data), the data decoded; ``[DONE]`` as the string."""
    out: list[tuple[str | None, Any]] = []
    for block in text.split("\n\n"):
        name: str | None = None
        data: str | None = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if data is not None:
            out.append((name, data if data == "[DONE]" else json.loads(data)))
    return out


def _streamed_text(events: list[tuple[str | None, Any]]) -> str:
    return "".join(
        (choice.get("delta") or {}).get("content") or ""
        for name, data in events if name is None and isinstance(data, dict)
        for choice in data.get("choices") or []
    )


def _receipt_events(events: list[tuple[str | None, Any]]) -> list[dict[str, Any]]:
    return [
        data["value"] for name, data in events
        if name == "faden" and isinstance(data, dict) and data.get("name") == text_receipts.EVENT
    ]


def _ends_with_the_receipt(events: list[tuple[str | None, Any]]) -> dict[str, Any]:
    """The receipt event's value, which comes after every chunk of the answer and right before ``[DONE]``."""
    assert events[-1] == (None, "[DONE]")
    name, data = events[-2]
    assert name == "faden" and data["type"] == "CUSTOM" and data["name"] == text_receipts.EVENT, events[-2]
    assert len(_receipt_events(events)) == 1
    return data["value"]


# ── The client ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_client_asks_for_a_receipt_with_the_hash_only() -> None:
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        signed = await ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN).receipt(
            content_sha256=text_sha256(ANSWER), model="qwen3.8-27b-fp8", agent="agent-7",
            watermark={"applied": "none", "reason": "short-text"},
        )

    assert isinstance(signed, SignedReceipt)
    assert signed.receipt_id == fake.receipts[0]
    assert signed.to_dict() == {"receipt": signed.receipt, "signature": signed.signature}
    request = fake.requests[0]
    assert request.url == httpx.URL(f"{BASE}{RECEIPTS}")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.extensions["timeout"]["read"] == 5.0
    assert fake.asked == [{
        "kind": "text", "model": "qwen3.8-27b-fp8", "model_version": None, "agent": "agent-7",
        "content_sha256": text_sha256(ANSWER), "watermark": {"applied": "none", "reason": "short-text"},
    }]
    assert ANSWER not in request.content.decode()


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "code"), [
    *FAILURES,
    pytest.param(_answer(422, {"detail": [{"msg": "bad"}]}), "invalid_request", id="refused-body"),
    pytest.param(_answer(200, {"receipt": {"id": "r-1"}}), "unreadable_answer", id="no-signature"),
    pytest.param(_answer(200, {"receipt": {"id": "r 1;x"}, "signature": "s"}), "unreadable_answer", id="bad-id"),
    pytest.param(lambda request: httpx.Response(200, text="not json"), "unreadable_answer", id="not-json"),
])
async def test_the_client_raises_why_no_receipt_came(failure: Answer, code: str) -> None:
    fake = _Engine()
    fake.fail = failure
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        with pytest.raises(ProvenanceRequestError) as failed:
            await ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN).receipt(
                content_sha256="0" * 64, model="m", watermark={"applied": "none", "reason": "short-text"},
            )
    assert failed.value.code == code


@pytest.mark.anyio
async def test_the_client_signs_records_and_keeps_placements_for_the_agent() -> None:
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        signed = await client.sign_record(RECORD, agent="agent-7")
        await client.report_placement(PLACEMENT, agent="agent-7")
        policy = await client.policy()

    assert signed["record"]["id"] == "rec-1" and signed["signature"] == "eyJ.rec-1.sig"
    assert fake.bodies[RECORDS] == [{**RECORD, "agent": "agent-7"}]
    assert fake.bodies[PLACEMENTS] == [{**PLACEMENT, "agent": "agent-7"}]
    assert policy == POLICY
    assert [(r.method, r.url.path) for r in fake.requests] == [
        ("POST", RECORDS), ("POST", PLACEMENTS), ("GET", POLICY_PATH),
    ]


@pytest.mark.anyio
async def test_the_anchors_are_kept_a_minute() -> None:
    fake = _Engine()
    now = [1000.0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN, clock=lambda: now[0])
        assert await client.anchors() == ANCHORS
        now[0] += 59
        assert await client.anchors() == ANCHORS
        assert len(fake.requests) == 1, "read once within the minute"
        now[0] += 2
        await client.anchors()
        assert len(fake.requests) == 2


@pytest.mark.anyio
async def test_anchors_without_their_chains_cannot_be_read() -> None:
    fake = _Engine()
    fake.fail = _answer(200, {"contract": "provenance/v1"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        with pytest.raises(ProvenanceRequestError) as failed:
            await ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN).anchors()
    assert failed.value.code == "unreadable_answer"


# ── The watermark ─────────────────────────────────────────────────


def _route(
    provider: ProviderType = ProviderType.VLLM, *, metadata: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None, litellm_provider: str | None = None,
) -> Any:
    return SimpleNamespace(
        provider_type=provider, node_metadata=metadata, extra_params=extra, litellm_provider=litellm_provider,
    )


#: Every row of the engine's table (docs/api.md, "What the gateway puts in a receipt's watermark").
TABLE = [
    pytest.param(_route(metadata={"watermark": True}), {}, 200, {"applied": "model"}, id="local-that-marks"),
    pytest.param(_route(metadata={"watermark": True}), {}, 199, {"applied": "none", "reason": "short-text"},
                 id="short-text"),
    pytest.param(_route(metadata={"watermark": True}), {"temperature": 0}, 400,
                 {"applied": "none", "reason": "no-watermark-greedy"}, id="temperature-0"),
    pytest.param(_route(metadata={"watermark": True}), {"use_beam_search": True}, 400,
                 {"applied": "none", "reason": "no-watermark-greedy"}, id="beam-search"),
    pytest.param(_route(), {}, 400, {"applied": "none", "reason": "runtime-without-watermark"},
                 id="local-that-does-not-mark"),
    pytest.param(_route(ProviderType.REMOTE_ANTHROPIC), {}, 400, {"applied": "upstream", "vendor": "anthropic"},
                 id="anthropic"),
    pytest.param(_route(ProviderType.REMOTE_OPENAI), {}, 400,
                 {"applied": "none", "reason": "no-watermark-remote-model"}, id="other-remote"),
]


@pytest.mark.parametrize(("route", "sent", "tokens", "expected"), TABLE)
def test_the_watermark_follows_the_engines_table(
    route: Any, sent: dict[str, Any], tokens: int, expected: dict[str, Any],
) -> None:
    assert text_receipts.watermark(route, sent, completion_tokens=tokens, text=LONG) == expected


def test_the_rows_apply_in_the_tables_order() -> None:
    marks = _route(metadata={"watermark": True})
    greedy = {"temperature": 0.0}
    assert text_receipts.watermark(marks, greedy, completion_tokens=50, text=LONG)["reason"] == "short-text"
    for route in (_route(), _route(ProviderType.REMOTE_ANTHROPIC), _route(ProviderType.REMOTE_OPENAI)):
        assert text_receipts.watermark(route, greedy, completion_tokens=400, text=LONG)["reason"] == (
            "no-watermark-greedy"
        )


@pytest.mark.parametrize("extra", [
    {"temperature": 0}, {"top_k": 1}, {"extra_body": {"use_beam_search": True}},
], ids=["temperature", "top-k", "extra-body"])
def test_greedy_decoding_set_by_the_route_counts(extra: dict[str, Any]) -> None:
    route = _route(metadata={"watermark": True}, extra=extra)
    assert text_receipts.watermark(route, {"temperature": 0.7}, completion_tokens=400, text=LONG) == {
        "applied": "none", "reason": "no-watermark-greedy",
    }


def test_a_deployment_started_with_the_watermark_names_its_key_version_and_context_width() -> None:
    """What the backend publishes (``node_metadata["watermark"]``) is what the engine checks a text with."""
    started = _route(metadata={"watermark": {
        "algorithm": "dual_key_gumbel", "key_version": 2, "context_width": 4, "other": "dropped",
    }})
    assert text_receipts.watermark(started, {}, completion_tokens=400, text=LONG) == {
        "applied": "model", "algorithm": "dual_key_gumbel", "key_version": 2, "context_width": 4,
    }
    odd = _route(metadata={"watermark": {"algorithm": "gumbel", "key_version": True, "context_width": -1}})
    assert text_receipts.watermark(odd, {}, completion_tokens=400, text=LONG) == {
        "applied": "model", "algorithm": "gumbel",
    }


def test_a_route_that_names_its_algorithm_or_vendor_is_said_so() -> None:
    gumbel = _route(metadata={"watermark": "dual_key_gumbel"})
    assert text_receipts.watermark(gumbel, {}, completion_tokens=400, text=LONG) == {
        "applied": "model", "algorithm": "dual_key_gumbel",
    }
    custom = _route(ProviderType.REMOTE_CUSTOM, litellm_provider="anthropic")
    assert text_receipts.watermark(custom, {}, completion_tokens=400, text=LONG) == {
        "applied": "upstream", "vendor": "anthropic",
    }
    assert text_receipts.watermark(_route(), {"temperature": 0.7}, completion_tokens=400, text=LONG)["reason"] == (
        "runtime-without-watermark"
    )


@pytest.mark.anyio
async def test_a_receipt_that_cannot_be_made_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    fake = _Engine()
    broken = _route(metadata=["not", "a", "mapping"])  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        with caplog.at_level(logging.WARNING, logger=text_receipts.__name__):
            signed = await text_receipts.sign(
                client, LONG, candidate=broken, sent={}, completion_tokens=400, model="m", agent=None,
                request_id="r-1",
            )
    assert signed is None
    assert fake.requests == []
    assert "(AttributeError)" in caplog.text and LONG not in caplog.text


def test_without_usage_the_tokens_are_counted_from_the_text() -> None:
    marks = _route(metadata={"watermark": True})
    assert text_receipts.watermark(marks, {}, completion_tokens=None, text=ANSWER)["reason"] == "short-text"
    assert text_receipts.watermark(marks, {}, completion_tokens=None, text=LONG) == {"applied": "model"}


# ── Whole answers ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_whole_answer_carries_its_receipt_in_the_header(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch)

    r = await _ask(client)

    assert r.status_code == 200, r.text
    assert r.headers[picture_marking.HEADER] == engine.receipts[0]
    assert r.json()["choices"][0]["message"] == {"role": "assistant", "content": ANSWER}
    assert "faden" not in r.json(), "a client that did not ask for events gets the OpenAI shape"
    assert engine.asked == [{
        "kind": "text", "model": ALIAS, "model_version": None, "agent": None,
        "content_sha256": text_sha256(ANSWER), "watermark": {"applied": "none", "reason": "short-text"},
    }]


@pytest.mark.anyio
async def test_a_whole_answer_gets_the_event_beside_it_when_events_are_asked(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch)

    r = await _ask(client, events=True)

    assert r.status_code == 200, r.text
    events = r.json()["faden"]["events"]
    receipt = [e for e in events if e.get("name") == text_receipts.EVENT]
    assert len(receipt) == 1 and events[-1] == receipt[0], "after the answer's other events"
    assert receipt[0]["type"] == "CUSTOM"
    assert receipt[0]["value"]["receipt"]["id"] == engine.receipts[0] == r.headers[picture_marking.HEADER]
    assert receipt[0]["value"]["signature"].startswith("eyJ")


@pytest.mark.anyio
async def test_a_whole_answers_text_parts_are_hashed_in_turn(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch, content=[{"type": "text", "text": "Page 12 holds "}, {"type": "text", "text": "the motor."}])

    r = await _ask(client)

    assert r.status_code == 200, r.text
    assert engine.asked[0]["content_sha256"] == text_sha256("Page 12 holds the motor.")


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_the_hash_is_of_the_text_the_client_got(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, engine: _Engine, pii: None,
) -> None:
    await _seeded(fastapi_app, db_session, ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"))
    _answers(monkeypatch, "Hello [PERSON_1], the pump is due.")

    r = await client.post("/v1/chat/completions", headers=_auth(**EVENTS), json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "I am Alice"}],
    })

    assert r.status_code == 200, r.text
    await _settled()
    assert [b["content_sha256"] for b in engine.asked] == [text_sha256("Hello Alice, the pump is due.")]
    assert "Alice" not in json.dumps(engine.asked), "the engine gets a hash, never the text"


# ── Streamed answers ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_streamed_answer_ends_with_its_receipt_before_done(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    state: dict[str, Any] = {}
    asked_after: list[bool] = []
    engine.on_receipt = lambda: asked_after.append(bool(state.get("last_token")))
    _answers(monkeypatch, LONG, completion_tokens=250, state=state)

    r = await _ask(client, stream=True, events=True)

    assert r.status_code == 200, r.text
    events = _sse(r.text)
    assert _streamed_text(events) == LONG, "not a character lost"
    value = _ends_with_the_receipt(events)
    assert set(value) == {"receipt", "signature"}
    assert value["receipt"]["id"] == engine.receipts[0]
    assert value["receipt"]["watermark"] == {"applied": "none", "reason": "runtime-without-watermark"}
    assert asked_after == [True], "asked for after the last token"
    assert engine.asked[0]["content_sha256"] == text_sha256(LONG)
    assert engine.asked[0]["model"] == ALIAS and engine.asked[0]["agent"] is None


@pytest.mark.anyio
async def test_a_stream_through_the_gateways_tool_rounds_ends_with_its_receipt(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    """A remote route is offered the gateway's tools: the answer streams round by round."""
    await _seeded(fastapi_app, db_session, ProviderType.REMOTE_ANTHROPIC)
    _answers(monkeypatch, LONG, completion_tokens=250)

    r = await _ask(client, stream=True, events=True)

    events = _sse(r.text)
    assert _streamed_text(events) == LONG
    value = _ends_with_the_receipt(events)
    assert value["receipt"]["watermark"] == {"applied": "upstream", "vendor": "anthropic"}


@pytest.mark.anyio
async def test_a_client_that_reads_no_events_gets_the_plain_stream_and_the_receipt_is_kept(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch)

    r = await _ask(client, stream=True)

    events = _sse(r.text)
    assert all(name is None for name, _ in events), "no named event for an OpenAI client"
    assert events[-1] == (None, "[DONE]")
    assert _streamed_text(events) == ANSWER
    await _settled()
    assert [b["content_sha256"] for b in engine.asked] == [text_sha256(ANSWER)], "asked for all the same"


@pytest.mark.anyio
async def test_a_chat_pages_reply_has_its_receipt_before_done(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    """The chat page's reply runs on its own and is read from the buffer, by the page and by one that comes back."""
    await _seeded(fastapi_app, db_session)
    sid = await _session(db_session)
    await db_session.commit()
    _answers(monkeypatch)

    r = await client.post("/v1/chat/completions", headers=_auth(**EVENTS), json={
        "model": ALIAS, "stream": True, "session_id": str(sid), "background": True,
        "messages": [{"role": "user", "content": "What is on page 12?"}],
    })

    assert r.status_code == 200, r.text
    assert r.headers.get("x-faden-turn-id")
    live = _sse(r.text)
    assert _streamed_text(live) == ANSWER
    assert _ends_with_the_receipt(live)["receipt"]["id"] == engine.receipts[0]
    reply = (await _replies(db_session, sid, _ended))[-1]
    assert (reply.status, reply.content) == ("complete", ANSWER)

    back = await client.get(f"/v1/sessions/{sid}/stream", headers=_auth())
    assert _ends_with_the_receipt(_sse(back.text))["receipt"]["id"] == engine.receipts[0]
    assert len(engine.asked) == 1


# ── Every row of the table, as the engine hears it ────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(("provider", "metadata", "body", "tokens", "expected"), [
    pytest.param(ProviderType.VLLM, {"watermark": "gumbel"}, {}, 250, {"applied": "model", "algorithm": "gumbel"},
                 id="local-that-marks"),
    pytest.param(ProviderType.VLLM, {"watermark": "gumbel"}, {}, 120, {"applied": "none", "reason": "short-text"},
                 id="short-text"),
    pytest.param(ProviderType.VLLM, {"watermark": "gumbel"}, {"temperature": 0}, 250,
                 {"applied": "none", "reason": "no-watermark-greedy"}, id="temperature-0"),
    pytest.param(ProviderType.VLLM, {"watermark": "gumbel"}, {"use_beam_search": True}, 250,
                 {"applied": "none", "reason": "no-watermark-greedy"}, id="beam-search"),
    pytest.param(ProviderType.VLLM, None, {}, 250, {"applied": "none", "reason": "runtime-without-watermark"},
                 id="local-that-does-not-mark"),
    pytest.param(ProviderType.REMOTE_ANTHROPIC, None, {}, 250, {"applied": "upstream", "vendor": "anthropic"},
                 id="anthropic"),
    pytest.param(ProviderType.REMOTE_OPENAI, None, {}, 250,
                 {"applied": "none", "reason": "no-watermark-remote-model"}, id="other-remote"),
])
async def test_the_engine_hears_the_watermark_of_each_row(
    provider: ProviderType, metadata: dict[str, Any] | None, body: dict[str, Any], tokens: int,
    expected: dict[str, Any],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session, provider, node_metadata=metadata)
    _answers(monkeypatch, LONG, completion_tokens=tokens)

    r = await _ask(client, **body)

    assert r.status_code == 200, r.text
    assert [b["watermark"] for b in engine.asked] == [expected]


# ── The engine failing ────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(("failure", "code"), [*FAILURES, pytest.param(_raise(RuntimeError), "RuntimeError", id="bug")])
async def test_an_engine_that_fails_never_fails_a_whole_answer(
    failure: Answer, code: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine, caplog: pytest.LogCaptureFixture,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch)
    engine.fail = failure

    with caplog.at_level(logging.WARNING, logger=text_receipts.__name__):
        r = await _ask(client)

    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == ANSWER
    assert picture_marking.HEADER not in r.headers
    lines = [rec for rec in caplog.records if rec.name == text_receipts.__name__]
    assert len(lines) == 1, "one line"
    assert f"({code})" in lines[0].getMessage()
    assert ANSWER not in caplog.text, "never the text"


@pytest.mark.anyio
@pytest.mark.parametrize("events", [True, False], ids=["events", "plain"])
async def test_an_engine_that_fails_never_fails_a_streamed_answer(
    events: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine, caplog: pytest.LogCaptureFixture,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch)
    engine.fail = _raise(httpx.ConnectError)

    with caplog.at_level(logging.WARNING, logger=text_receipts.__name__):
        r = await _ask(client, stream=True, events=events)
        await _settled()

    assert r.status_code == 200
    streamed = _sse(r.text)
    assert _streamed_text(streamed) == ANSWER
    if events:
        assert _ends_with_the_receipt(streamed) == {"receipt": None, "status": "unsigned", "reason": "server-unavailable"}
    else:
        assert _receipt_events(streamed) == [] and streamed[-1] == (None, "[DONE]")
    lines = [rec for rec in caplog.records if rec.name == text_receipts.__name__]
    assert len(lines) == 1 and "(unreachable)" in lines[0].getMessage()
    assert ANSWER not in caplog.text


# ── Answers that get no receipt ───────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_an_answer_of_tool_calls_only_gets_no_receipt(
    stream: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch, "", tool_calls=[TOOL_CALL])

    r = await _ask(client, stream=stream, events=True, tools=[READ_PAGE])

    assert r.status_code == 200, r.text
    await _settled()
    assert engine.asked == []
    if stream:
        assert _receipt_events(_sse(r.text)) == []
    else:
        assert picture_marking.HEADER not in r.headers
        assert r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "read_page"
        assert not [e for e in r.json()["faden"]["events"] if e.get("name") == text_receipts.EVENT]


@pytest.mark.anyio
async def test_a_stream_that_breaks_gets_no_receipt(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,
) -> None:
    await _seeded(fastapi_app, db_session)
    _answers(monkeypatch, LONG, fail_after=3)

    r = await _ask(client, stream=True, events=True)

    events = _sse(r.text)
    assert events[-2][1]["error"]["code"] == "upstream_stream_failed"
    assert events[-1] == (None, "[DONE]")
    await _settled()
    assert _receipt_events(events) == [] and engine.asked == [], "only a complete answer is receipted"


# ── An agent's answers ────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_an_agents_answer_names_the_agent(
    stream: bool,
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    _answers(monkeypatch)

    r = await client.post("/v1/agents/eplan-assistant/chat/completions", headers=_headers(**EVENTS), json={
        "model": "agent", "stream": stream, "messages": [{"role": "user", "content": "What is on page 12?"}],
    })

    assert r.status_code == 200, r.text
    assert [(b["agent"], b["model"]) for b in engine.asked] == [(AGENT_ID, ALIAS)]
    if stream:
        assert _ends_with_the_receipt(_sse(r.text))["receipt"]["id"] == engine.receipts[0]
    else:
        assert r.headers[picture_marking.HEADER] == engine.receipts[0]


# ── The module off ────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_with_the_module_off_an_answer_is_as_before(
    stream: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seeded(fastapi_app, db_session)
    monkeypatch.setattr(fastapi_app.state, "provenance_client", None, raising=False)
    _answers(monkeypatch)

    r = await _ask(client, stream=stream, events=True)

    assert r.status_code == 200, r.text
    assert picture_marking.HEADER not in r.headers
    if stream:
        events = _sse(r.text)
        assert _receipt_events(events) == [] and events[-1] == (None, "[DONE]")
        assert _streamed_text(events) == ANSWER
    else:
        assert not [e for e in r.json()["faden"]["events"] if e.get("name") == text_receipts.EVENT]
        assert r.json()["choices"][0]["message"]["content"] == ANSWER


# ── The public routes ─────────────────────────────────────────────

POLICY_URL = "/v1/agents/eplan-assistant/provenance/policy"
RECORDS_URL = "/v1/agents/eplan-assistant/provenance/records"
PLACEMENTS_URL = "/v1/agents/eplan-assistant/provenance/placements"
ANCHORS_URL = "/v1/provenance/anchors"
VERIFY_URL = "/v1/provenance/verify"
AGENT_VERIFY_URL = "/v1/agents/eplan-assistant/provenance/verify"

#: The agent routes, each with a body it takes.
AGENT_ROUTES = [
    pytest.param("get", POLICY_URL, None, id="policy"),
    pytest.param("post", RECORDS_URL, RECORD, id="records"),
    pytest.param("post", PLACEMENTS_URL, PLACEMENT, id="placements"),
]


@pytest.mark.anyio
async def test_the_agents_sdk_reads_the_engines_default_policy(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.get(POLICY_URL, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json() == POLICY


@pytest.mark.anyio
async def test_a_record_is_signed_for_the_agent_the_route_names(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(RECORDS_URL, headers=_headers(), json=RECORD)

    assert r.status_code == 200, r.text
    assert r.json()["record"]["id"] == "rec-1"
    assert r.json()["signature"] == "eyJ.rec-1.sig"
    assert engine.bodies[RECORDS] == [{**RECORD, "model": None, "agent": AGENT_ID}]


@pytest.mark.anyio
@pytest.mark.parametrize("body", [
    pytest.param({**RECORD, "agent": "another-agent"}, id="names-an-agent"),
    pytest.param({**RECORD, "content_sha256": "not-a-hash"}, id="not-a-hash"),
    pytest.param({**RECORD, "edit_kind": "invented"}, id="unknown-edit-kind"),
    pytest.param({"media_type": "text/plain", "edit_kind": "generated"}, id="no-hash"),
])
async def test_a_record_the_contract_does_not_take_is_refused_before_the_engine(
    body: dict[str, Any], client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(RECORDS_URL, headers=_headers(), json=body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert engine.requests == []


@pytest.mark.anyio
async def test_a_placement_is_kept_for_the_agent(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(PLACEMENTS_URL, headers=_headers(), json=PLACEMENT)

    assert r.status_code == 202, r.text
    assert r.json() == {"stored": True}
    layers = [{"reason": None, **layer} for layer in PLACEMENT["layers"]]  # type: ignore[attr-defined]
    assert engine.bodies[PLACEMENTS] == [{**PLACEMENT, "layers": layers, "agent": AGENT_ID}]
    r = await client.post(PLACEMENTS_URL, headers=_headers(), json={**PLACEMENT, "text": "the content"})
    assert r.status_code == 400, "never the content"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), AGENT_ROUTES)
async def test_the_agent_routes_authenticate_as_the_other_agent_routes(
    method: str, url: str, body: dict[str, Any] | None,
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    backend.put(_profile(id=OTHER_AGENT_ID, slug="tia-assistant"))
    refused = [
        ({}, 401, "missing_authorization"),
        (_headers(_agent_token(OTHER_AGENT_ID)), 403, "agent_mismatch"),
        (_headers(_login_token(USER)), 403, "agent_token_required"),
    ]
    for headers, status, code in refused:
        r = await client.request(method, url, headers=headers, json=body)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text
    assert engine.requests == []

    for headers in (_headers(), _headers(_login_token(MAINTAINER))):
        r = await client.request(method, url, headers=headers, json=body)
        assert r.status_code in (200, 202), r.text


@pytest.mark.anyio
async def test_the_anchors_need_no_sign_in(
    client: AsyncClient, engine: _Engine,
) -> None:
    r = await client.get(ANCHORS_URL)
    assert r.status_code == 200, r.text
    assert r.json() == ANCHORS
    assert r.headers["cache-control"] == "public, max-age=60"
    await client.get(ANCHORS_URL)
    assert len(engine.requests) == 1, "kept a minute"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), [
    *AGENT_ROUTES, pytest.param("get", ANCHORS_URL, None, id="anchors"),
])
async def test_with_the_module_off_each_route_says_so(
    method: str, url: str, body: dict[str, Any] | None,
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    backend: Any,  # noqa: F811 - the fixture
) -> None:
    monkeypatch.setattr(fastapi_app.state, "provenance_client", None, raising=False)
    r = await client.request(method, url, headers=_headers(), json=body)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["type"] == "provenance_unavailable"
    assert r.json()["error"]["code"] == "provenance_off"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), [
    *AGENT_ROUTES, pytest.param("get", ANCHORS_URL, None, id="anchors"),
])
@pytest.mark.parametrize(("failure", "code"), FAILURES)
async def test_an_engine_that_fails_is_a_503_on_each_route(
    method: str, url: str, body: dict[str, Any] | None, failure: Answer, code: str,
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    engine.fail = failure
    r = await client.request(method, url, headers=_headers(), json=body)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["type"] == "provenance_unavailable"
    assert r.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_a_record_the_engine_does_not_take_is_a_400(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    engine.fail = _answer(422, {"detail": [{"msg": "bad"}]})
    r = await client.post(RECORDS_URL, headers=_headers(), json=RECORD)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_provenance_request"


# ── Checking content ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_signed_in_user_checks_a_text_and_nothing_else_goes_to_the_engine(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(VERIFY_URL, headers=_headers(_login_token(USER)), json={"text": LONG, "signed": True})

    assert r.status_code == 200, r.text
    assert r.json() == VERIFIED
    assert r.headers["cache-control"] == "no-store"
    assert engine.bodies[VERIFY_PATH] == [{"text": LONG, "signed": True}]


@pytest.mark.anyio
async def test_an_agent_checks_a_file_on_its_own_route(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    body = {"file": "aGVsbG8=", "media_type": "image/png"}
    r = await client.post(AGENT_VERIFY_URL, headers=_headers(), json=body)
    assert r.status_code == 200, r.text
    assert engine.bodies[VERIFY_PATH] == [{**body, "signed": False}]


@pytest.mark.anyio
async def test_checking_needs_a_sign_in_and_an_agent_token_only_on_its_route(
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(VERIFY_URL, json={"text": LONG})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "missing_authorization")
    r = await client.post(VERIFY_URL, headers=_headers(), json={"text": LONG})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "agent_token_not_allowed")
    r = await client.post(AGENT_VERIFY_URL, headers=_headers(_login_token(USER)), json={"text": LONG})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "agent_token_required")
    assert engine.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("body", [
    pytest.param({}, id="nothing"),
    pytest.param({"text": "a", "file": "aGVsbG8=", "media_type": "image/png"}, id="both"),
    pytest.param({"file": "aGVsbG8="}, id="file-without-media-type"),
    pytest.param({"text": "a", "keep": True}, id="unknown-field"),
])
async def test_a_check_the_contract_does_not_take_is_refused_before_the_engine(
    body: dict[str, Any], client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    r = await client.post(VERIFY_URL, headers=_headers(_login_token(USER)), json=body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert engine.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(("answer", "status", "code"), [
    pytest.param(_answer(413, {"detail": "too big"}), 413, "too_large", id="too-large"),
    pytest.param(_answer(422, {"detail": [{"msg": "bad"}]}), 400, "invalid_provenance_request", id="refused"),
    pytest.param(_answer(503, {"detail": {"code": "busy", "message": "Busy"}}), 503, "busy", id="busy"),
    pytest.param(_answer(200, {"no": "verdict"}), 503, "unreadable_answer", id="unreadable"),
])
async def test_a_check_the_engine_cannot_do_says_why(
    answer: Answer, status: int, code: str,
    client: AsyncClient, backend: Any, engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    engine.fail = answer
    r = await client.post(VERIFY_URL, headers=_headers(_login_token(USER)), json={"text": LONG})
    assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text


@pytest.mark.anyio
async def test_with_the_module_off_checking_says_so(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    backend: Any,  # noqa: F811 - the fixture
) -> None:
    monkeypatch.setattr(fastapi_app.state, "provenance_client", None, raising=False)
    r = await client.post(VERIFY_URL, headers=_headers(_login_token(USER)), json={"text": LONG})
    assert (r.status_code, r.json()["error"]["code"]) == (503, "provenance_off")
