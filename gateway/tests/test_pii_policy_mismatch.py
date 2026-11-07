"""A PII policy the installed detector refuses is named as such, not as a PII service that is down.

The PII service answers 422 with a code when the policy does not fit its detector (a threshold on Presidio's scale
for a local model, a type it does not find). That used to read as "PII service unavailable", and every request to a
remote model on an install with a local detector was refused that way (found 31 October 2026 on the release VM).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.pii_client import POLICY_REFUSALS, PIIClient, PIIPolicyMismatchError
from faden_gateway.services.gateway.pii_policy import parse_pii_policy
from faden_gateway.services.registry import service_registry
from tests.test_pii_module_off import (
    _chat,
    _model_answers,
    _Observability,
    _policy,
    _scan_tool_results,
    _seed,
    _tool_request,
)


def _answering(status: int, body: Any) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _request: httpx.Response(status, json=body))


async def _sanitize(transport: httpx.MockTransport) -> None:
    async with httpx.AsyncClient(transport=transport) as http:
        policy = parse_pii_policy(_policy())
        assert policy is not None
        await PIIClient(base_url="http://pii.local", http_client=http).sanitize(
            payload={"messages": [{"role": "user", "content": "I am Alice Meyer"}]}, policy=policy,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("code", sorted(POLICY_REFUSALS))
async def test_a_refusal_of_the_policy_is_told_apart(code: str) -> None:
    with pytest.raises(PIIPolicyMismatchError) as refused:
        await _sanitize(_answering(422, {"detail": {"code": code}}))
    assert refused.value.code == code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (422, {"detail": {"code": "pii_input_too_large"}}),
        (422, {"detail": [{"msg": "field required"}]}),
        (503, {"detail": {"code": "pii_unavailable"}}),
        (500, {}),
    ],
    ids=["too-large", "validation", "unavailable", "server-error"],
)
async def test_other_failures_stay_failures(status: int, body: Any) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _sanitize(_answering(status, body))


@pytest.mark.anyio
async def test_the_chat_is_refused_with_what_to_do(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("block"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def refused(self: PIIClient, **kwargs: object) -> Any:  # noqa: ARG001
        raise PIIPolicyMismatchError("pii_unsupported_profile")

    monkeypatch.setattr(PIIClient, "sanitize", refused)
    try:
        r = await _chat(client)
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 502
    error = r.json()["error"]
    assert error["code"] == "pii_policy_mismatch"
    assert "pii_unsupported_profile" in error["message"] and "PII policy page" in error["message"]
    assert sent == [], "nothing reached the provider"


@pytest.mark.anyio
async def test_a_policy_that_allows_lets_the_chat_through_as_before(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail_action still decides: a refused policy is a failed scan."""
    await _seed(db_session, pii=_policy("allow"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def refused(self: PIIClient, **kwargs: object) -> Any:  # noqa: ARG001
        raise PIIPolicyMismatchError("pii_profile_required")

    monkeypatch.setattr(PIIClient, "sanitize", refused)
    try:
        r = await _chat(client)
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 200
    assert "Alice Meyer" in json.dumps(sent[0])


@pytest.mark.anyio
async def test_tool_results_are_refused_with_what_to_do() -> None:
    class Refusing:
        async def sanitize(self, **kwargs: object) -> Any:  # noqa: ARG002
            raise PIIPolicyMismatchError("pii_profile_required")

    req = _tool_request(_policy("block"), saved=True)
    answers = [{"role": "tool", "content": "Alice Meyer lives in Munich"}]
    from types import SimpleNamespace  # noqa: PLC0415

    from faden_gateway.services.gateway.service import GatewayService  # noqa: PLC0415

    with pytest.raises(GatewayError) as stopped:
        await GatewayService._scan_tool_results(SimpleNamespace(pii_client=Refusing()), req, answers)  # type: ignore[arg-type]
    assert stopped.value.code == "pii_policy_mismatch"
    assert await _scan_tool_results(_tool_request(_policy("allow"), saved=True), answers) == answers
