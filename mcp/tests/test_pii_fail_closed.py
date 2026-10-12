"""The personal-data check fails closed.

Redact and Block used to pass the original arguments on whenever the PII
service was not configured or did not answer, so "Redact" quietly sent
personal data to the tool whenever the optional ``pii`` module was off.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from faden_mcp.services.dao import MCPDao
from faden_mcp.services.privacy_proxy import (
    CHECK_UNAVAILABLE,
    CHECK_UNAVAILABLE_MESSAGE,
    PrivacyProxy,
    default_pii_mode,
)
from faden_mcp.web.api.internal import views

ARGS: dict[str, Any] = {"to": "Anna Schmidt"}


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _answering(body: Any, status: int = 200) -> httpx.AsyncClient:
    return _client(lambda request: httpx.Response(status, json=body))


def _refused(decision: Any) -> bool:
    return (
        not decision.allowed
        and decision.sanitized_args == {}
        and decision.redaction_summary["reason"] == CHECK_UNAVAILABLE
    )


# ── The proxy ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redact", "block"])
async def test_no_service_url_refuses(mode: str) -> None:
    proxy = PrivacyProxy(pii_base_url="", http_client=_answering({}))
    assert _refused(await proxy.check(arguments=ARGS, pii_mode=mode))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redact", "block"])
async def test_unreachable_service_refuses(mode: str) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=_client(down))
    assert _refused(await proxy.check(arguments=ARGS, pii_mode=mode))


@pytest.mark.asyncio
async def test_timeout_refuses() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=_client(slow))
    assert _refused(await proxy.check(arguments=ARGS, pii_mode="redact"))


@pytest.mark.asyncio
async def test_service_error_refuses() -> None:
    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=_answering({}, 500))
    assert _refused(await proxy.check(arguments=ARGS, pii_mode="redact"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [[], {}, {"entities_found": "many"}, {"entities_found": None}],
)
async def test_unusable_answer_refuses(body: Any) -> None:
    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=_answering(body))
    assert _refused(await proxy.check(arguments=ARGS, pii_mode="redact"))


@pytest.mark.asyncio
async def test_a_non_json_answer_refuses() -> None:
    client = _client(lambda request: httpx.Response(200, text="<html>proxy error</html>"))
    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=client)
    assert _refused(await proxy.check(arguments=ARGS, pii_mode="block"))


@pytest.mark.asyncio
async def test_success_sends_the_redacted_arguments() -> None:
    body = {
        "entities_found": 1,
        "sanitized_payload": {"messages": [{"content": json.dumps({"to": "<PERSON>"})}]},
    }
    proxy = PrivacyProxy(pii_base_url="http://pii", http_client=_answering(body))
    decision = await proxy.check(arguments=ARGS, pii_mode="redact")
    assert decision.allowed
    assert decision.sanitized_args == {"to": "<PERSON>"}


@pytest.mark.asyncio
async def test_nothing_found_passes_the_arguments() -> None:
    proxy = PrivacyProxy(
        pii_base_url="http://pii", http_client=_answering({"entities_found": 0}),
    )
    decision = await proxy.check(arguments=ARGS, pii_mode="redact")
    assert decision.allowed
    assert decision.sanitized_args == ARGS


@pytest.mark.asyncio
async def test_block_mode_blocks_found_data() -> None:
    proxy = PrivacyProxy(
        pii_base_url="http://pii", http_client=_answering({"entities_found": 2}),
    )
    decision = await proxy.check(arguments=ARGS, pii_mode="block")
    assert not decision.allowed
    assert decision.redaction_summary["action"] == "blocked"
    assert "reason" not in decision.redaction_summary


@pytest.mark.asyncio
async def test_allow_mode_passes_through_without_asking_the_service() -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("allow mode must not call the PII service")

    for base in ("", "http://pii"):
        proxy = PrivacyProxy(pii_base_url=base, http_client=_client(never))
        decision = await proxy.check(arguments=ARGS, pii_mode="allow")
        assert decision.allowed
        assert decision.sanitized_args == ARGS


# ── The default mode of a new server ──────────────────────────────────


@pytest.mark.asyncio
async def test_default_is_redact_when_the_service_answers() -> None:
    client = _client(lambda request: httpx.Response(200, json={}))
    mode = await default_pii_mode(
        configured="", pii_base_url="http://pii", http_client=client,
    )
    assert mode == "redact"


@pytest.mark.asyncio
async def test_default_is_allow_when_the_service_is_off() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name not resolved")

    for base, client in (
        ("http://pii", _client(down)),
        ("http://pii", _answering({}, 503)),
        ("", _client(down)),
    ):
        mode = await default_pii_mode(
            configured="", pii_base_url=base, http_client=client,
        )
        assert mode == "allow"


@pytest.mark.asyncio
async def test_a_configured_default_wins() -> None:
    mode = await default_pii_mode(
        configured="block", pii_base_url="", http_client=_answering({}),
    )
    assert mode == "block"


# ── The tool call ─────────────────────────────────────────────────────


class _Manager:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def get(self, server_id: str) -> object:
        return object()

    async def call_tool(self, *a: Any) -> dict[str, Any]:
        self.sent.append(a[3])
        return {"content": [{"type": "text", "text": "ok"}]}


def _request(manager: _Manager, client: httpx.AsyncClient) -> SimpleNamespace:
    state = SimpleNamespace(connection_manager=manager, http_client=client)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _stub_dao(monkeypatch: pytest.MonkeyPatch, pii_mode: str) -> None:
    server = SimpleNamespace(id=uuid.uuid4(), name="s", enabled=True, pii_mode=pii_mode)
    tool = SimpleNamespace(
        server_id=server.id, enabled=True, upstream_name="t", raw_schema_json=None,
    )

    async def get_tool(self: MCPDao, **_: Any) -> SimpleNamespace:
        return tool

    async def get_server(self: MCPDao, server_id: uuid.UUID) -> SimpleNamespace:
        return server

    monkeypatch.setattr(MCPDao, "get_tool_by_qualified_name", get_tool)
    monkeypatch.setattr(MCPDao, "get_server", get_server)


async def _call(manager: _Manager, client: httpx.AsyncClient) -> Any:
    return await views.call_tool(
        body=views.ToolCallRequest(qualified_name="mcp.s.t", arguments=ARGS),
        request=_request(manager, client),  # type: ignore[arg-type]
        session=SimpleNamespace(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a_redact_call_without_the_service_is_refused_with_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 with the advice as detail: the gateway tells the model, the chat shows it."""
    from faden_mcp.settings import settings  # noqa: PLC0415

    monkeypatch.setattr(settings, "pii_service_url", "")
    _stub_dao(monkeypatch, "redact")
    manager = _Manager()
    with pytest.raises(HTTPException) as refused:
        await _call(manager, _answering({}))
    assert refused.value.status_code == 503
    assert refused.value.detail == CHECK_UNAVAILABLE_MESSAGE
    assert "PII mode to Allow" in CHECK_UNAVAILABLE_MESSAGE
    assert manager.sent == [], "the tool must not be called"


@pytest.mark.asyncio
async def test_an_allow_call_without_the_service_goes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from faden_mcp.settings import settings  # noqa: PLC0415

    monkeypatch.setattr(settings, "pii_service_url", "")
    _stub_dao(monkeypatch, "allow")
    manager = _Manager()
    result = await _call(manager, _answering({}))
    assert not result.is_error
    assert manager.sent == [ARGS]
