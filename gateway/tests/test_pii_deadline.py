"""A PII check that ran out of time is named as such, not as a PII service that is down.

The PII service answers 504 `pii_deadline_exceeded` when it cannot finish a request within its time limit (on a CPU,
a long text). That read as "PII service unavailable and fail_action=block" (found 18 November 2026: a Nextcloud
agent's turn with an included document, with the check on for local traffic).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.pii_client import DEADLINE_REFUSALS, PIIClient, PIIDeadlineExceededError
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
from tests.test_pii_policy_mismatch import _answering, _sanitize


@pytest.mark.anyio
@pytest.mark.parametrize("code", sorted(DEADLINE_REFUSALS))
async def test_running_out_of_time_is_told_apart(code: str) -> None:
    with pytest.raises(PIIDeadlineExceededError) as refused:
        await _sanitize(_answering(504, {"detail": {"code": code}}))
    assert refused.value.code == code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body"),
    [(504, {"detail": {"code": "something_else"}}), (504, {}), (503, {"detail": {"code": "pii_deadline_exceeded"}})],
    ids=["other-code", "no-code", "not-504"],
)
async def test_other_504s_stay_failures(status: int, body: Any) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _sanitize(_answering(status, body))


@pytest.mark.anyio
async def test_the_chat_is_refused_naming_the_time_limit(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, pii=_policy("block"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def refused(self: PIIClient, **kwargs: object) -> Any:  # noqa: ARG001
        raise PIIDeadlineExceededError("pii_deadline_exceeded")

    monkeypatch.setattr(PIIClient, "sanitize", refused)
    try:
        r = await _chat(client)
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 504
    error = r.json()["error"]
    assert error["code"] == "pii_deadline_exceeded"
    assert "longer than its limit" in error["message"] and "unavailable" not in error["message"]
    assert sent == [], "nothing reached the provider"


@pytest.mark.anyio
async def test_a_policy_that_allows_lets_the_chat_through_as_before(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail_action still decides: a check that ran out of time is a failed scan."""
    await _seed(db_session, pii=_policy("allow"))
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, sent)
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def refused(self: PIIClient, **kwargs: object) -> Any:  # noqa: ARG001
        raise PIIDeadlineExceededError("pii_deadline_exceeded")

    monkeypatch.setattr(PIIClient, "sanitize", refused)
    try:
        r = await _chat(client)
    finally:
        service_registry.configure("pii", enabled=False, url=None)

    assert r.status_code == 200
    assert "Alice Meyer" in json.dumps(sent[0])


@pytest.mark.anyio
async def test_tool_results_out_of_time_are_refused_naming_the_limit() -> None:
    class Refusing:
        async def sanitize(self, **kwargs: object) -> Any:  # noqa: ARG002
            raise PIIDeadlineExceededError("pii_deadline_exceeded")

    req = _tool_request(_policy("block"), saved=True)
    answers = [{"role": "tool", "content": "Alice Meyer lives in Munich"}]
    from faden_gateway.services.gateway.service import GatewayService  # noqa: PLC0415

    with pytest.raises(GatewayError) as stopped:
        await GatewayService._scan_tool_results(SimpleNamespace(pii_client=Refusing()), req, answers)  # type: ignore[arg-type]
    assert stopped.value.code == "pii_deadline_exceeded" and stopped.value.status_code == 504
    assert "longer than its limit" in stopped.value.message
    assert await _scan_tool_results(_tool_request(_policy("allow"), saved=True), answers) == answers
