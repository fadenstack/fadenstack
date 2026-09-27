"""A cluster's Serve behind its head's model-TLS proxy: what the backend asks for, and what it publishes.

Serve binds once, when it starts. So the backend asks for the loopback address
(and the proxy in front) wherever the head's agent can run the proxy, and then
publishes what the agent reports it did -- a cluster whose Serve already listens
on the network keeps being reached there, over plain HTTP, until an
administrator moves it (``services/inference/model_tls.move``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from llm_port_backend.services import machine_tls
from llm_port_backend.services.inference.drivers.ray import deployment as dep


def _facts(*, model_tls: bool, config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        environment=SimpleNamespace(config_json=config or {"serve_http_port": 8010}, address="10.100.0.2:6390"),
        head_host="10.250.10.49",
        spec_data={},
        model=SimpleNamespace(display_name="Qwen2.5-0.5B-Instruct"),
        model_tls=model_tls,
    )


def _manager() -> dep.RayDeploymentManager:
    return dep.RayDeploymentManager.__new__(dep.RayDeploymentManager)


def test_behind_the_proxy_serve_binds_loopback_and_the_proxy_takes_the_port() -> None:
    options = _manager()._serve_options(_facts(model_tls=True))  # noqa: SLF001
    assert options["http_options"] == {"host": "127.0.0.1", "port": 28010}
    assert options["model_tls"] == {"public_port": 8010, "internal_port": 28010}


def test_without_the_proxy_nothing_changes() -> None:
    options = _manager()._serve_options(_facts(model_tls=False))  # noqa: SLF001
    assert options["http_options"] == {"host": "0.0.0.0", "port": 8010}  # noqa: S104
    assert "model_tls" not in options


def test_the_internal_port_stays_out_of_rays_and_the_ephemeral_ranges() -> None:
    assert machine_tls.internal_port(8000) == 28000
    with pytest.raises(ValueError, match="too high"):
        machine_tls.internal_port(12800)
    assert machine_tls.supported({"model_tls": 1})
    assert not machine_tls.supported({}) and not machine_tls.supported(None)


class _Endpoints:
    """EndpointDAO, as far as _publish_endpoint uses it."""

    rows: list[Any] = []
    updates: list[dict[str, Any]] = []

    def __init__(self, _session: Any) -> None:
        pass

    async def list_for_deployment(self, _deployment_id: Any) -> list[Any]:
        return list(self.rows)

    async def create(self, deployment_id: Any, **kw: Any) -> None:
        self.rows.append(SimpleNamespace(id=uuid.uuid4(), name=kw["name"], **{k: v for k, v in kw.items() if k != "name"}))

    async def update(self, _endpoint_id: Any, **kw: Any) -> None:
        self.updates.append(kw)


async def _published(monkeypatch: pytest.MonkeyPatch, *, model_tls: bool, run: dict[str, Any]) -> dict[str, Any]:
    _Endpoints.rows, _Endpoints.updates = [], []
    monkeypatch.setattr(dep, "EndpointDAO", _Endpoints)
    deployment = SimpleNamespace(id=uuid.uuid4(), observed_status_json={"observation": {"run": run}})
    await _manager()._publish_endpoint(  # noqa: SLF001
        None, deployment, _facts(model_tls=model_tls), "llmport-dep1", ready_replicas=1,
    )
    return _Endpoints.updates[-1]


@pytest.mark.anyio
async def test_a_model_behind_the_proxy_is_published_https_for_the_gateways_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "route_prefix": "/llmport-dep1",
        "model_tls": {"protected": True, "public_port": 8010, "internal_port": 28010},
        "serve_http": {"host": "127.0.0.1", "port": 28010},
    }
    update = await _published(monkeypatch, model_tls=True, run=run)
    assert update["address"] == "https://10.250.10.49:8010/llmport-dep1"
    assert update["published"]["machine_tls"] is True
    assert update["published"]["model_tls_pending"] is False
    assert update["published"]["base_url"] == "https://10.250.10.49:8010/llmport-dep1/v1"


@pytest.mark.anyio
async def test_a_serve_from_before_is_published_where_it_listens_and_marked_to_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = {
        "route_prefix": "/llmport-dep1",
        "model_tls": {"protected": False, "reason": "serve_running", "public_port": 8010},
        "serve_http": {"host": "0.0.0.0", "port": 8010},  # noqa: S104
    }
    update = await _published(monkeypatch, model_tls=True, run=run)
    assert update["address"] == "http://10.250.10.49:8010/llmport-dep1"
    assert update["published"]["machine_tls"] is False
    assert update["published"]["model_tls_pending"] is True


@pytest.mark.anyio
async def test_a_loopback_only_port_is_never_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent that bound loopback but could not put the proxy up says so; the port it bound is not the address."""
    run = {"route_prefix": "/llmport-dep1", "serve_http": {"host": "127.0.0.1", "port": 28010}}
    update = await _published(monkeypatch, model_tls=True, run=run)
    assert ":28010" not in update["address"]


@pytest.mark.anyio
async def test_an_older_agent_reports_nothing_and_is_published_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    update = await _published(monkeypatch, model_tls=False, run={"route_prefix": "/llmport-dep1"})
    assert update["address"] == "http://10.250.10.49:8010/llmport-dep1"
    assert update["published"]["machine_tls"] is False
    assert update["published"]["model_tls_pending"] is False
