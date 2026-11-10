"""The dashboard's Grafana probe on an install: "Down: grafana" while Grafana ran.

The probe asked ``FADEN_BACKEND_GRAFANA_URL``, which an install never sets (it is Grafana's address for the
browser, where it differs from the console's own ``/grafana``), and answered "unknown"; the dashboard shows
anything but "up" as down (a test server, 0.6.0 and 0.7.0rc1, 9-10 November 2026). Without that setting it now
asks Grafana where an install runs it: ``faden-grafana:3000`` on the compose network, under ``/grafana``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from faden_backend.services.dashboard import platform
from faden_backend.settings import settings


class _Client:
    """httpx.AsyncClient answering with *answer* (a status code or an exception), recording the URLs asked."""

    asked: list[str] = []
    answer: Any = 200

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        _Client.asked.append(url)
        if isinstance(_Client.answer, Exception):
            raise _Client.answer
        return httpx.Response(_Client.answer, request=httpx.Request("GET", url))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    _Client.asked, _Client.answer = [], 200
    monkeypatch.setattr(platform.httpx, "AsyncClient", _Client)
    return _Client


@pytest.mark.anyio
async def test_an_install_asks_grafana_on_the_compose_network(
    client: type[_Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "grafana_url", None)

    dependency = await platform._grafana()

    assert client.asked == ["http://faden-grafana:3000/grafana/api/health"]
    assert (dependency.name, dependency.status) == ("grafana", "up")


@pytest.mark.anyio
async def test_a_set_grafana_address_is_asked_as_before(client: type[_Client], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "grafana_url", "http://127.0.0.1:3001/grafana")

    await platform._grafana()

    assert client.asked == ["http://127.0.0.1:3001/grafana"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "status"),
    [(503, "degraded"), (httpx.ConnectError("no such host"), "down")],
)
async def test_grafana_unwell_or_away_is_said(
    client: type[_Client], monkeypatch: pytest.MonkeyPatch, answer: Any, status: str
) -> None:
    monkeypatch.setattr(settings, "grafana_url", None)
    client.answer = answer

    assert (await platform._grafana()).status == status
