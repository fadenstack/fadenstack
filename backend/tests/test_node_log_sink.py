"""Where a machine sends its logs.

The one-line install writes no Loki address, so machines installed that way
forwarded nothing: on the DGX pair the last Ray log in Loki predated the
reinstall. The agent now asks the backend, and the backend has to answer with
an address that works *from the machine* -- its own ``loki_base_url`` is a
loopback address in development and a container name in the deployment.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.settings import settings
from faden_backend.web.api.node_files import views
from faden_backend.web.api.node_files.views import agent_log_sink


@pytest.mark.parametrize(
    ("base", "request_host", "expected"),
    [
        # Development: Loki on the backend's own loopback, published on the host.
        ("http://127.0.0.1:3100", "10.250.10.220", "http://10.250.10.220:3100"),
        ("http://localhost:3100", "10.250.10.220", "http://10.250.10.220:3100"),
        # The deployment: a compose service name only the backend can resolve.
        ("http://faden-loki:3100", "faden.example.com", "http://faden.example.com:3100"),
        # A Loki the machines can already reach is passed on as it is.
        ("https://loki.corp.example:3100/", "10.0.0.5", "https://loki.corp.example:3100"),
        ("http://10.0.0.9:3100", "10.0.0.5", "http://10.0.0.9:3100"),
        # IPv6 hosts need brackets.
        ("http://[::1]:3100", "fd00::5", "http://[fd00::5]:3100"),
    ],
)
def test_the_address_works_from_the_machine(base: str, request_host: str, expected: str) -> None:
    assert agent_log_sink(base, "", request_host) == expected


def test_an_explicit_address_wins() -> None:
    assert agent_log_sink("http://127.0.0.1:3100", " http://logs.lan:3100 ", "x") == "http://logs.lan:3100"


def test_no_answer_rather_than_a_wrong_one() -> None:
    assert agent_log_sink("http://127.0.0.1:3100", "", None) is None
    assert agent_log_sink("", "", "10.0.0.5") is None


@pytest.mark.anyio()
async def test_the_endpoint_answers_with_the_address_the_agent_used(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "loki_base_url", "http://127.0.0.1:3100")
    monkeypatch.setattr(settings, "agent_loki_url", "")
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(id="n1")
    try:
        response = await client.get(
            "/api/node-files/log-sink", headers={"host": "10.250.10.220:8000"},
        )
    finally:
        fastapi_app.dependency_overrides.pop(views._authenticate_node, None)

    assert response.status_code == 200
    assert response.json() == {"loki_url": "http://10.250.10.220:3100"}


@pytest.mark.anyio()
async def test_the_endpoint_needs_a_node_credential(client: AsyncClient) -> None:
    response = await client.get("/api/node-files/log-sink")
    # Refused, and no address given. (The test app has no database, so node
    # authentication fails closed with 503 before it reads the credential.)
    assert response.status_code in (401, 403, 503)
    assert "loki_url" not in response.text


# ── through the backend (agents 0.1.17 and later) ──────────────────────────


@pytest.mark.anyio()
async def test_an_agent_that_asks_is_sent_to_the_backend_itself(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushed straight to Loki, logs were plain HTTP to a Loki with no authentication -- and failed
    wherever Loki was published on the server's loopback only (the release VM: every 5 s)."""
    monkeypatch.setattr(settings, "loki_base_url", "http://faden-loki:3100")
    monkeypatch.setattr(settings, "agent_loki_url", "")
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(id="n1")
    try:
        asked = await client.get("/api/node-files/log-sink", params={"via": "backend"})
        older = await client.get("/api/node-files/log-sink", headers={"host": "10.250.10.61"})
    finally:
        fastapi_app.dependency_overrides.pop(views._authenticate_node, None)
    assert asked.json() == {"loki_url": "/api/node-files/logs", "via": "backend"}
    assert older.json() == {"loki_url": "http://10.250.10.61:3100"}, "an older agent is answered as before"


@pytest.mark.anyio()
async def test_a_push_is_forwarded_to_loki_as_the_machine_that_sent_it(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    forwarded: list[httpx.Request] = []

    def loki(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(settings, "loki_base_url", "http://faden-loki:3100")
    monkeypatch.setattr(views, "_loki_http", httpx.AsyncClient(transport=httpx.MockTransport(loki)))
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(
        id="n1", host="10.250.10.49", agent_id="gpu-02",
    )
    push = {"streams": [{"stream": {"job": "node-agent", "host": "10.250.10.71"}, "values": [["1", "hello"]]}]}
    try:
        response = await client.post("/api/node-files/logs/loki/api/v1/push", json=push)
        bad = await client.post("/api/node-files/logs/loki/api/v1/push", json={"lines": ["x"]})
    finally:
        fastapi_app.dependency_overrides.pop(views._authenticate_node, None)
        monkeypatch.setattr(views, "_loki_http", None)
    assert response.status_code == 204
    assert str(forwarded[0].url) == "http://faden-loki:3100/loki/api/v1/push"
    import json

    sent = json.loads(forwarded[0].content)
    assert sent["streams"][0]["stream"] == {"job": "node-agent", "host": "10.250.10.49"}, "no machine speaks for another"
    assert bad.status_code == 400 and len(forwarded) == 1
