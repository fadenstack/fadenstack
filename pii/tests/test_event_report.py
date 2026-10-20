"""The PII service reports scan events to the backend with the internal token."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from faden_pii.settings import settings
from faden_pii.web.api.pii import views


class _Recorder:
    def __init__(self, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(self.status)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(views, "_get_event_client", lambda: rec)
    monkeypatch.setattr(settings, "backend_url", "http://backend:8000")
    return rec


@pytest.mark.anyio
async def test_event_is_sent_with_the_bearer_token(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backend_token", "secret-token")
    await views._log_event_async(operation="scan", duration_ms=14.25)
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["url"] == "http://backend:8000/api/admin/pii/events/log"
    assert recorder.calls[0]["headers"] == {"Authorization": "Bearer secret-token"}
    assert recorder.calls[0]["json"]["duration_ms"] == 14.25


@pytest.mark.anyio
async def test_no_token_sends_nothing_and_does_not_fail(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backend_token", "")
    monkeypatch.setattr(settings, "environment", "production")
    await views._log_event_async(operation="scan")
    assert recorder.calls == []


@pytest.mark.anyio
async def test_dev_environment_uses_the_dev_token(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backend_token", "")
    monkeypatch.setattr(settings, "environment", "dev")
    await views._log_event_async(operation="scan")
    assert recorder.calls[0]["headers"] == {"Authorization": "Bearer dev-internal-token"}


@pytest.mark.anyio
async def test_a_refusal_is_not_an_error(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backend_token", "wrong")
    recorder.status = 401
    await views._log_event_async(operation="scan")  # must not raise
    assert len(recorder.calls) == 1
