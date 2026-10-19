"""The provenance module: registered and on by default, switched off only when confirmed, every switch recorded.

Also what the backend asks the engine (its info route, with the service token) and that an engine which does
not answer is a state for the console, never an error.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.users import User, _signed_in_active_user
from faden_backend.services.core_modules import PROVENANCE_DISABLE_WARNING, register_core_modules
from faden_backend.services.module_registry import ModuleDef, module_registry
from faden_backend.services.provenance import client as provenance_client
from faden_backend.services.system_settings import runtime_mapping
from faden_backend.services.system_settings.registry import registry_by_key
from faden_backend.settings import Settings, settings
from faden_backend.web.api.admin.dependencies import get_docker
from faden_backend.web.api.admin.provenance import views as provenance_views
from faden_backend.web.api.admin.services import views as services_views
from faden_backend.web.api.admin.system.views import (
    _refuse_unconfirmed_switch_off,
    get_system_settings_service,
)

CAPABILITIES = {"images": False, "receipts": False, "records": False, "detect": False}
INFO = {"contract": "provenance/v1", "engine_version": "0.5.0", "capabilities": CAPABILITIES, "plugins": []}


def _provenance() -> ModuleDef:
    register_core_modules()
    mod = module_registry.get_module("provenance")
    assert mod is not None
    return mod


async def _person(db: AsyncSession, *, superuser: bool = True) -> User:
    user = User(
        id=uuid.uuid4(), email=f"admin-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=superuser,
    )
    db.add(user)
    await db.flush()
    return user


async def _switches(db: AsyncSession) -> list[AuditEvent]:
    return list((await db.execute(
        select(AuditEvent).where(AuditEvent.target_type == "module").order_by(AuditEvent.seq),
    )).scalars().all())


# ── Registration and settings ──────────────────────────────────────────────────────


def test_the_module_is_registered_like_the_other_container_modules() -> None:
    mod = _provenance()
    assert mod.module_type == "container"
    assert mod.compose_profile == "provenance"
    assert mod.compose_services == ["faden-provenance", "faden-provenance-migrator"]
    assert mod.container_names == ["faden-provenance"]
    assert mod.settings_flag == "provenance_enabled"
    assert mod.disable_warning == PROVENANCE_DISABLE_WARNING
    assert "Art. 50(2)" in mod.disable_warning
    assert mod.on_enable is not None and mod.on_disable is not None
    # Only the module that asks before it is switched off carries a warning.
    assert all(m.disable_warning is None for m in module_registry.list_modules() if m.name != "provenance")


def test_the_module_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FADEN_BACKEND_PROVENANCE_ENABLED", "FADEN_BACKEND_PROVENANCE_SERVICE_URL"):
        monkeypatch.delenv(name, raising=False)
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    assert fresh.provenance_enabled is True
    assert fresh.provenance_service_url == "http://127.0.0.1:8009"
    by_key = registry_by_key()
    assert by_key["faden_gateway.provenance_enabled"].default is True
    assert by_key["faden_gateway.provenance_service_url"].default == "http://faden-provenance:8000"
    token = by_key["faden_gateway.provenance_service_token"]
    assert token.is_secret and token.protected


def test_a_published_service_token_is_refused_outside_development() -> None:
    from faden_backend.published_secrets import problems

    def install(**values: object) -> Settings:
        base: dict[str, object] = {
            "environment": "production", "settings_master_key": "a-key-of-this-install-0123456789-abcdefghij",
            "mcp_enabled": False, "skills_enabled": False, "allow_published_keys": False,
            "provenance_service_token": "a-token-of-this-install",
        }
        return Settings(**{**base, **values})  # type: ignore[arg-type]

    assert problems(install()) == []
    for published in ("dev-provenance-service-token", "dev-provenance-service-token-change-me"):
        (found,) = problems(install(provenance_service_token=published))
        assert "FADEN_BACKEND_PROVENANCE_SERVICE_TOKEN" in found
    published = "dev-provenance-service-token"
    assert problems(install(provenance_enabled=False, provenance_service_token=published)) == []
    assert problems(install(environment="dev", provenance_service_token=published)) == []


def test_the_settings_keys_reach_the_runtime_settings() -> None:
    assert runtime_mapping.resolve_runtime_attr("faden_gateway.provenance_enabled") == "provenance_enabled"
    assert runtime_mapping.resolve_runtime_attr("faden_gateway.provenance_service_url") == "provenance_service_url"
    assert runtime_mapping.resolve_runtime_attr("faden_gateway.provenance_service_token") == "provenance_service_token"


def test_the_engine_counts_as_a_service_of_the_release() -> None:
    from faden_backend.services.updates.versions import CORE_SERVICES

    assert "faden-provenance" in CORE_SERVICES


def test_the_engine_container_is_classified_as_a_platform_service() -> None:
    from faden_backend.db.models.containers import ContainerClass
    from faden_backend.web.api.admin.containers.views import _classify_by_name

    assert _classify_by_name("faden-provenance") == (ContainerClass.SYSTEM_AUX, "platform")
    assert _classify_by_name("faden-provenance-migrator") == (ContainerClass.SYSTEM_AUX, "platform")


# ── What the engine says ────────────────────────────────────────────────────────────


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_the_engine_is_asked_with_the_service_token() -> None:
    seen: list[httpx.Request] = []

    def engine(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={**INFO, "plugins": ["acme", {"name": "hsm"}]})

    info = await provenance_client.engine_info(
        "http://faden-provenance:8000/", "the-token", transport=_transport(engine),
    )
    assert [str(r.url) for r in seen] == ["http://faden-provenance:8000/api/v1/provenance/info"]
    assert seen[0].headers["Authorization"] == "Bearer the-token"
    assert info.state == "running"
    assert info.as_dict() == {
        "state": "running",
        "contract": "provenance/v1",
        "engine_version": "0.5.0",
        "capabilities": CAPABILITIES,
        "plugins": ["acme", "hsm"],
        "contract_supported": True,
        "expected_contract": "provenance/v1",
    }


@pytest.mark.anyio
async def test_an_engine_that_does_not_answer_is_not_running() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def starting(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="starting")

    for handler in (down, starting):
        info = await provenance_client.engine_info("http://x", "t", transport=_transport(handler))
        assert info.state == "not_running"
        assert info.contract is None and info.capabilities == {} and not info.contract_supported


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "state"),
    [
        (httpx.Response(401, json={"detail": "bad token"}), "refused"),
        (httpx.Response(403), "refused"),
        (httpx.Response(404, json={"detail": "Not Found"}), "unexpected"),
        (httpx.Response(200, text="<html>a proxy page</html>"), "unexpected"),
        (httpx.Response(200, json={"status": "ok"}), "unexpected"),
        (httpx.Response(200, json=["provenance/v1"]), "unexpected"),
    ],
)
async def test_other_answers_are_states_too(answer: httpx.Response, state: str) -> None:
    info = await provenance_client.engine_info("http://x", "t", transport=_transport(lambda _r: answer))
    assert info.state == state


@pytest.mark.anyio
async def test_an_engine_on_another_contract_is_reported_as_such() -> None:
    other = {**INFO, "contract": "provenance/v2"}
    info = await provenance_client.engine_info("http://x", "t", transport=_transport(
        lambda _r: httpx.Response(200, json=other),
    ))
    assert info.state == "running"
    assert info.contract == "provenance/v2" and not info.contract_supported


@pytest.mark.anyio
async def test_no_token_is_sent_when_none_is_set() -> None:
    seen: list[httpx.Request] = []

    def engine(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401)

    info = await provenance_client.engine_info("http://x", "", transport=_transport(engine))
    assert "Authorization" not in seen[0].headers
    assert info.state == "refused"


@pytest.mark.anyio
async def test_the_status_route_reports_the_engine(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin
    monkeypatch.setattr(settings, "provenance_enabled", True)

    async def running() -> provenance_client.EngineInfo:
        return provenance_client.EngineInfo(
            state="running", contract="provenance/v1", engine_version="0.5.0",
            capabilities=dict(CAPABILITIES),
        )

    monkeypatch.setattr(provenance_views, "engine_info", running)
    r = await client.get(fastapi_app.url_path_for("provenance_status"))
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["enabled"] is True and body["state"] == "running"
    assert body["contract"] == "provenance/v1" and body["contract_supported"] is True
    assert body["capabilities"] == CAPABILITIES


@pytest.mark.anyio
async def test_the_status_route_answers_when_the_engine_is_down(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    real = provenance_client.engine_info

    async def unreachable() -> provenance_client.EngineInfo:
        return await real("http://faden-provenance:8000", "t", transport=_transport(down))

    monkeypatch.setattr(provenance_views, "engine_info", unreachable)
    r = await client.get(fastapi_app.url_path_for("provenance_status"))
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["state"] == "not_running"
    assert r.json()["contract"] is None


@pytest.mark.anyio
async def test_the_status_route_needs_the_modules_right(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    person = await _person(dbsession, superuser=False)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: person
    r = await client.get(fastapi_app.url_path_for("provenance_status"))
    assert r.status_code == status.HTTP_403_FORBIDDEN


# ── Switching it on and off ────────────────────────────────────────────────────────


@pytest.fixture
def switch(
    fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[Any]]:
    """The services routes with compose, Docker and the settings sync answered here; returns what was asked."""
    calls: dict[str, list[Any]] = {"compose": [], "sync": []}

    async def compose(*args: str, profile: str | None = None) -> tuple[int, str, str]:
        calls["compose"].append((profile, *args))
        return 0, "", ""

    async def states(_docker: Any, names: list[str]) -> list[dict[str, str]]:
        return [{"name": n, "state": "exited"} for n in names]

    async def sync(_service: Any, enabled: bool, actor_id: Any) -> list[str]:
        calls["sync"].append((enabled, actor_id))
        return []

    monkeypatch.setattr(services_views, "_run_compose", compose)
    monkeypatch.setattr(services_views, "_container_states", states)
    for mod in (_provenance(), module_registry.get_module("skills")):
        monkeypatch.setattr(mod, "on_enable", sync)
        monkeypatch.setattr(mod, "on_disable", sync)
    fastapi_app.dependency_overrides[get_docker] = _nothing
    fastapi_app.dependency_overrides[get_system_settings_service] = _nothing
    return calls


def _nothing() -> object:
    """Stands in for Docker and the settings service: the faked calls never use them."""
    return object()


@pytest.mark.anyio
async def test_the_manifest_says_which_module_asks_before_it_is_switched_off(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
    switch: dict[str, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    class NoDocker:  # the manifest's Docker call fails: containers are "unknown"
        async def list_containers(self, all_: bool = False) -> list[dict[str, Any]]:
            raise RuntimeError("no Docker here")

    fastapi_app.dependency_overrides[get_docker] = NoDocker

    async def unhealthy(_url: str) -> str:
        return "unhealthy"

    monkeypatch.setattr(services_views, "_probe_health", unhealthy)
    r = await client.get("/api/admin/services")
    assert r.status_code == status.HTTP_200_OK, r.text
    by_name = {s["name"]: s for s in r.json()["services"]}
    assert by_name["provenance"]["disable_warning"] == PROVENANCE_DISABLE_WARNING
    assert by_name["skills"]["disable_warning"] is None


@pytest.mark.anyio
async def test_switching_it_off_without_confirmation_is_refused_and_recorded(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, switch: dict[str, list[Any]],
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    for body in (None, {"confirm": False}):
        r = await client.put("/api/admin/services/provenance/disable", json=body)
        assert r.status_code == status.HTTP_409_CONFLICT, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "confirmation_required"
        assert "Art. 50(2)" in detail["message"]

    assert switch["sync"] == [] and switch["compose"] == [], "nothing was switched"
    events = await _switches(dbsession)
    assert [(e.action, e.result, e.target_id) for e in events] == [
        ("module.disable", AuditResult.DENY, "provenance"),
    ] * 2
    assert events[0].actor_email == admin.email and events[0].time is not None
    assert events[0].severity == "high"
    assert json.loads(events[0].metadata_json or "{}")["confirmed"] is False


@pytest.mark.anyio
async def test_switching_it_off_with_confirmation_switches_and_records_who(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, switch: dict[str, list[Any]],
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    r = await client.put("/api/admin/services/provenance/disable", json={"confirm": True})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["errors"] == [] and r.json()["stopped"] == ["faden-provenance"]
    assert switch["sync"] == [(False, admin.id)]
    assert switch["compose"] == [("provenance", "stop", "faden-provenance", "faden-provenance-migrator")]

    (event,) = await _switches(dbsession)
    assert (event.action, event.result, event.target_id) == ("module.disable", AuditResult.ALLOW, "provenance")
    assert event.actor_email == admin.email and event.severity == "high"
    details = json.loads(event.metadata_json or "{}")
    assert details["confirmed"] is True
    assert details["warning"] == PROVENANCE_DISABLE_WARNING


@pytest.mark.anyio
async def test_switching_it_on_is_recorded(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, switch: dict[str, list[Any]],
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    r = await client.put("/api/admin/services/provenance/enable")
    assert r.status_code == status.HTTP_200_OK, r.text
    assert switch["compose"] == [("provenance", "up", "-d", "--remove-orphans")]
    assert switch["sync"] == [(True, admin.id)]
    (event,) = await _switches(dbsession)
    assert (event.action, event.result, event.target_id) == ("module.enable", AuditResult.ALLOW, "provenance")
    assert event.actor_email == admin.email


@pytest.mark.anyio
async def test_other_modules_switch_off_without_confirmation_and_are_recorded(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, switch: dict[str, list[Any]],
) -> None:
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin

    r = await client.put("/api/admin/services/skills/disable")
    assert r.status_code == status.HTTP_200_OK, r.text
    assert switch["sync"] == [(False, admin.id)]
    (event,) = await _switches(dbsession)
    assert (event.action, event.target_id, event.severity) == ("module.disable", "skills", "normal")


@pytest.mark.anyio
async def test_switching_needs_the_modules_right(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, switch: dict[str, list[Any]],
) -> None:
    person = await _person(dbsession, superuser=False)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: person
    r = await client.put("/api/admin/services/provenance/disable", json={"confirm": True})
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert switch["sync"] == []


# ── The settings routes do not switch it off around the confirmation ─────────────────


def test_the_flag_cannot_be_switched_off_through_the_settings_routes() -> None:
    _provenance()
    with pytest.raises(HTTPException) as refused:
        _refuse_unconfirmed_switch_off("faden_gateway.provenance_enabled", False)
    assert refused.value.status_code == status.HTTP_409_CONFLICT
    assert refused.value.detail["code"] == "use_module_switch"  # type: ignore[index]
    # Switching it on, other modules' flags and other settings pass.
    _refuse_unconfirmed_switch_off("faden_gateway.provenance_enabled", True)
    _refuse_unconfirmed_switch_off("faden_gateway.skills_enabled", False)
    _refuse_unconfirmed_switch_off("faden_gateway.provenance_service_url", "http://elsewhere:8000")


@pytest.mark.anyio
async def test_the_settings_route_refuses_to_switch_it_off(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    _provenance()
    admin = await _person(dbsession)
    fastapi_app.dependency_overrides[_signed_in_active_user] = lambda: admin
    url = fastapi_app.url_path_for("system_settings_update", key="faden_gateway.provenance_enabled")
    r = await client.put(url, json={"value": False, "target_host": "local"})
    assert r.status_code == status.HTTP_409_CONFLICT, r.text
    assert r.json()["detail"]["code"] == "use_module_switch"

    r = await client.post(
        fastapi_app.url_path_for("system_wizard_apply"),
        json={"values": {"faden_gateway.provenance_enabled": False}, "target_host": "local"},
    )
    assert r.status_code == status.HTTP_409_CONFLICT, r.text
