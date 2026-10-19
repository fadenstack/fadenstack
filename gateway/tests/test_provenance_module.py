"""The provenance module: its settings, its client, its start-up and its line in the services manifest."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_gateway.published_secrets import problems
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.provenance_client import (
    CONTRACT,
    ProvenanceCapabilities,
    ProvenanceClient,
    ProvenanceContractError,
    ProvenanceUnavailableError,
)
from faden_gateway.services.registry import ServiceRegistry, ServiceStatus, service_registry
from faden_gateway.settings import Settings, settings
from faden_gateway.web.lifespan import _setup_provenance, _setup_service_registry

TOKEN = "provenance-token"
WRONG_TOKEN = "a-wrong-token"
PUBLISHED_TOKEN = "dev-provenance-service-token"
OWN_TOKEN = "a-token-of-this-install"
BASE = "http://faden-provenance:8000"

INFO = {
    "contract": "provenance/v1",
    "engine_version": "0.4.5",
    "capabilities": {"images": False, "receipts": False, "records": False, "detect": False},
    "plugins": [],
}


def _engine(
    info: dict[str, Any] | None = None, *, token: str = TOKEN, health: int = 200,
) -> Callable[[httpx.Request], httpx.Response]:
    """A provenance service as the contract describes it: 401 without a token, 403 with a wrong one."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(health, json={"status": "ok"})
        if request.url.path == "/api/v1/provenance/info":
            auth = request.headers.get("authorization")
            if auth is None:
                return httpx.Response(401, json={"detail": "Not authenticated"})
            if auth != f"Bearer {token}":
                return httpx.Response(403, json={"detail": "Forbidden"})
            return httpx.Response(200, json=INFO if info is None else info)
        return httpx.Response(404)

    return answer


def _client(http: httpx.AsyncClient, token: str = TOKEN) -> ProvenanceClient:
    return ProvenanceClient(base_url=BASE, http_client=http, service_token=token)


# ── Settings ──────────────────────────────────────────────────────


def test_the_module_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENABLED", "SERVICE_URL", "SERVICE_TOKEN"):
        monkeypatch.delenv(f"FADEN_GATEWAY_PROVENANCE_{name}", raising=False)
    loaded = Settings(_env_file=None)
    assert loaded.provenance_enabled is False
    assert loaded.provenance_service_url == "http://faden-provenance:8000"
    assert loaded.provenance_service_token == ""


def test_the_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_GATEWAY_PROVENANCE_ENABLED", "true")
    monkeypatch.setenv("FADEN_GATEWAY_PROVENANCE_SERVICE_URL", "http://127.0.0.1:8009")
    monkeypatch.setenv("FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN", "from-the-environment")
    loaded = Settings(_env_file=None)
    assert loaded.provenance_enabled is True
    assert loaded.provenance_service_url == "http://127.0.0.1:8009"
    assert loaded.provenance_service_token == "from-the-environment"


def _production(**values: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "settings_master_key": "a-key-of-this-install-0123456789-abcdefghij",
        "encryption_key": "a-chat-key-of-this-install-0123456789",
        "mcp_enabled": False,
        "skills_enabled": False,
        "provenance_enabled": False,
        "allow_published_keys": False,
    }
    return Settings(**{**base, **values})


def test_a_published_token_is_refused_while_the_module_is_on() -> None:
    refused = problems(_production(provenance_enabled=True, provenance_service_token=PUBLISHED_TOKEN))
    assert len(refused) == 1
    assert "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN" in refused[0]
    assert problems(_production(provenance_enabled=False, provenance_service_token=PUBLISHED_TOKEN)) == []
    assert problems(_production(provenance_enabled=True, provenance_service_token=OWN_TOKEN)) == []


# ── The client ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_info_is_read_with_the_token() -> None:
    seen: list[httpx.Request] = []
    engine = _engine()

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return engine(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        info = await _client(http).info()

    assert info.contract == CONTRACT
    assert info.engine_version == "0.4.5"
    assert info.capabilities == ProvenanceCapabilities(images=False, receipts=False, records=False, detect=False)
    assert info.plugins == ()
    assert seen[0].url == httpx.URL(f"{BASE}/api/v1/provenance/info")
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.anyio
async def test_a_capability_is_on_only_when_the_engine_says_true() -> None:
    sent = {**INFO, "capabilities": {"images": True, "receipts": "yes"}, "plugins": [{"name": "c2pa"}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine(sent))) as http:
        info = await _client(http).info()
    assert info.capabilities == ProvenanceCapabilities(images=True)
    assert info.plugins == ({"name": "c2pa"},)


@pytest.mark.anyio
async def test_an_unknown_contract_is_refused_with_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_engine({**INFO, "contract": "provenance/v2", "engine_version": "1.0.0"})),
    ) as http:
        client = _client(http)
        with caplog.at_level(logging.DEBUG, logger="faden_gateway.services.gateway.provenance_client"):
            with pytest.raises(ProvenanceContractError) as refused:
                await client.info()
            with pytest.raises(ProvenanceContractError):
                await client.info()
        assert await client.healthy() is True, "the service is up; the gateway just does not use it"

    assert refused.value.contract == "provenance/v2"
    assert refused.value.engine_version == "1.0.0"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1, "logged once, not on every read"
    assert "provenance/v2" in errors[0].getMessage()
    assert "provenance/v1" in errors[0].getMessage()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("token", "status", "says"),
    [("", 401, "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN is empty"),
     (WRONG_TOKEN, 403, "is not the service's token")],
)
async def test_a_refused_token_is_said_so(token: str, status: int, says: str) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        with pytest.raises(ProvenanceUnavailableError) as refused:
            await _client(http, token=token).info()
    assert refused.value.status_code == status
    assert refused.value.reached is True
    assert says in str(refused.value)


@pytest.mark.anyio
async def test_an_answer_without_a_contract_cannot_be_read() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine({"engine_version": "0.4.5"}))) as http:
        with pytest.raises(ProvenanceUnavailableError) as failed:
            await _client(http).info()
    assert not isinstance(failed.value, ProvenanceContractError)


@pytest.mark.anyio
async def test_a_timeout_is_unhealthy() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client = _client(http)
        assert await client.healthy() is False
        with pytest.raises(ProvenanceUnavailableError) as failed:
            await client.info()
        probed = await client.probe()
    assert failed.value.reached is False
    assert probed.status is ServiceStatus.UNHEALTHY


@pytest.mark.anyio
async def test_health_needs_an_ok() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        assert await _client(http).healthy() is True
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine(health=503))) as http:
        assert await _client(http).healthy() is False


# ── Start-up ──────────────────────────────────────────────────────


@pytest.fixture
def module_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Sets the module's settings for a start-up; the shared registry is left as it was found."""
    saved = {
        s.name: (s.enabled, s.url, s.status, s.version, dict(s.metadata))
        for s in service_registry.all_services()
    }

    def configure(*, enabled: bool, token: str = TOKEN, url: str = BASE) -> None:
        monkeypatch.setattr(settings, "provenance_enabled", enabled)
        monkeypatch.setattr(settings, "provenance_service_url", url)
        monkeypatch.setattr(settings, "provenance_service_token", token)

    yield configure
    for svc in service_registry.all_services():
        svc.enabled, svc.url, svc.status, svc.version, svc.metadata = saved[svc.name]
    service_registry.set_probe("provenance", None)


def _start(http: httpx.AsyncClient) -> SimpleNamespace:
    app = SimpleNamespace(state=SimpleNamespace(http_client=http))
    _setup_service_registry(app)  # type: ignore[arg-type]
    _setup_provenance(app)  # type: ignore[arg-type]
    return app


@pytest.mark.anyio
async def test_start_up_leaves_the_module_off_when_it_is_off(module_settings: Callable[..., None]) -> None:
    module_settings(enabled=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        app = _start(http)
    assert app.state.provenance_client is None
    assert service_registry.is_enabled("provenance") is False
    assert service_registry.get_info("provenance").status is ServiceStatus.DISABLED  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_start_up_configures_the_client_when_the_module_is_on(module_settings: Callable[..., None]) -> None:
    module_settings(enabled=True, url=f"{BASE}/")
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        app = _start(http)
        assert isinstance(app.state.provenance_client, ProvenanceClient)
        assert service_registry.get_url("provenance") == BASE
        assert (await app.state.provenance_client.info()).contract == CONTRACT


@pytest.mark.anyio
async def test_start_up_warns_about_an_empty_token(
    module_settings: Callable[..., None], caplog: pytest.LogCaptureFixture,
) -> None:
    module_settings(enabled=True, token="")
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        with caplog.at_level(logging.WARNING, logger="faden_gateway.web.lifespan"):
            app = _start(http)
    assert app.state.provenance_client is not None
    assert "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN is empty" in caplog.text


# ── The services manifest ─────────────────────────────────────────


def _manifest_line(registry: ServiceRegistry) -> dict[str, Any]:
    return next(s for s in registry.to_dict()["services"] if s["name"] == "provenance")


@pytest.mark.anyio
async def test_the_manifest_reports_the_engine_its_contract_and_capabilities() -> None:
    registry = ServiceRegistry()
    registry.configure("provenance", enabled=True, url=BASE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        registry.set_probe("provenance", _client(http).probe)
        results = await registry.check_health(http)

    assert results["provenance"] is ServiceStatus.HEALTHY
    line = _manifest_line(registry)
    assert line["status"] == "healthy"
    assert line["version"] == "0.4.5"
    assert line["metadata"] == {
        "contract": "provenance/v1",
        "capabilities": {"images": False, "receipts": False, "records": False, "detect": False},
        "plugins": [],
    }


@pytest.mark.anyio
async def test_the_manifest_reports_an_engine_the_gateway_does_not_use() -> None:
    registry = ServiceRegistry()
    registry.configure("provenance", enabled=True, url=BASE)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_engine({**INFO, "contract": "provenance/v2", "engine_version": "1.0.0"})),
    ) as http:
        registry.set_probe("provenance", _client(http).probe)
        await registry.check_health(http)

    line = _manifest_line(registry)
    assert line["status"] == "unhealthy"
    assert line["version"] == "1.0.0"
    assert line["metadata"]["contract"] == "provenance/v2"
    assert "provenance/v1" in line["metadata"]["error"]


@pytest.mark.anyio
async def test_the_manifest_says_why_the_token_is_refused() -> None:
    registry = ServiceRegistry()
    registry.configure("provenance", enabled=True, url=BASE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
        registry.set_probe("provenance", _client(http, token=WRONG_TOKEN).probe)
        await registry.check_health(http)
    line = _manifest_line(registry)
    assert line["status"] == "unhealthy"
    assert "403" in line["metadata"]["error"]


@pytest.mark.anyio
async def test_a_failing_probe_is_unhealthy_and_the_others_are_still_checked() -> None:
    async def broken() -> Any:
        raise RuntimeError("boom")

    registry = ServiceRegistry()
    registry.configure("provenance", enabled=True, url=BASE)
    registry.configure("skills", enabled=True, url="http://skills.local")
    registry.set_probe("provenance", broken)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        results = await registry.check_health(http)
    assert results == {"provenance": ServiceStatus.UNHEALTHY, "skills": ServiceStatus.HEALTHY}


@pytest.mark.anyio
async def test_switched_off_the_module_is_listed_disabled_without_details() -> None:
    registry = ServiceRegistry()
    line = _manifest_line(registry)
    assert line["enabled"] is False
    assert line["status"] == "disabled"
    assert line["url"] is None
    assert line["metadata"] == {}


@pytest.mark.anyio
async def test_the_services_route_lists_the_engine(fastapi_app: FastAPI, client: AsyncClient) -> None:
    registry = ServiceRegistry()
    registry.configure("provenance", enabled=True, url=BASE)
    fastapi_app.state.service_registry = registry
    signed_in = AuthContext(user_id="u1", tenant_id="default", raw_claims={}, token=OWN_TOKEN)
    fastapi_app.dependency_overrides[get_auth_context] = lambda: signed_in
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_engine())) as http:
            registry.set_probe("provenance", _client(http).probe)
            response = await client.get("/v1/services")
    finally:
        fastapi_app.dependency_overrides.pop(get_auth_context, None)
    assert response.status_code == 200
    line = next(s for s in response.json()["services"] if s["name"] == "provenance")
    assert line["status"] == "healthy"
    assert line["metadata"]["contract"] == "provenance/v1"
    assert line["metadata"]["capabilities"]["images"] is False
