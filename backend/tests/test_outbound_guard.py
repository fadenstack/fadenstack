"""Provider endpoints never point the server at link-local or cloud metadata addresses.

The connection test fetched any URL for anyone who could see providers (an
operator, a viewer) and told its failures apart: enough to map the internal
network and read a cloud's instance metadata. It is now for those who may add
a provider, and it, the saved endpoint and the health probe refuse link-local
and metadata addresses -- by name too, once resolved. LAN, loopback and
container addresses stay allowed: on-premises model servers are the design.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.llm_dao import ProviderDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.llm import LLMRuntime, ProviderTarget, ProviderType
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import outbound
from faden_backend.services.llm import remote_health
from faden_backend.services.llm.adapters.cloud import CloudAdapter
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.llm.service import LLMService
from faden_backend.web.api.llm.dependencies import get_llm_service

pytestmark = pytest.mark.anyio


# ── which addresses ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5", "192.168.1.20", "172.17.0.1", "127.0.0.1", "::1", "fd00::1",
        "100.64.1.2",  # CGNAT: Tailscale and the like put LAN machines here
        "8.8.8.8", "2001:4860:4860::8888",
    ],
)
def test_lan_loopback_and_public_addresses_are_called(address: str) -> None:
    assert outbound.refused_reason(address) is None


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("169.254.169.254", "metadata"),
        ("fd00:ec2::254", "metadata"),
        ("100.100.100.200", "metadata"),
        ("::ffff:169.254.169.254", "metadata"),
        ("64:ff9b::a9fe:a9fe", "metadata"),
        ("169.254.10.1", "link-local"),
        ("fe80::1", "link-local"),
        ("fe80::1%eth0", "link-local"),
        ("0.0.0.0", "unspecified"),  # noqa: S104 - an address refused, not one bound
        ("0.1.2.3", "unspecified"),
        ("::", "unspecified"),
    ],
)
def test_link_local_metadata_and_unspecified_addresses_are_not(address: str, reason: str) -> None:
    assert reason in (outbound.refused_reason(address) or "")


async def test_a_name_is_refused_when_any_of_its_addresses_is(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {
        "metadata.google.internal": ["169.254.169.254"],
        "two-faced.lan": ["10.0.0.7", "169.254.169.254"],
        "vllm.lan": ["10.0.0.7"],
    }

    async def resolve(host: str, **_: object) -> list[str]:
        return answers.get(host, [])

    monkeypatch.setattr(outbound, "resolve", resolve)

    with pytest.raises(outbound.RefusedAddressError, match="metadata"):
        await outbound.check_url("http://metadata.google.internal/computeMetadata/v1/")
    with pytest.raises(outbound.RefusedAddressError, match=r"two-faced\.lan \(169\.254\.169\.254\)"):
        await outbound.check_url("http://two-faced.lan:8000/v1")
    with pytest.raises(outbound.RefusedAddressError, match="link-local"):
        await outbound.check_url("http://[fe80::1]:8000")
    await outbound.check_url("http://vllm.lan:8000/v1")
    await outbound.check_url("http://unresolvable.lan:8000")  # fails by itself, later
    await outbound.check_url("litellm://openai")
    await outbound.check_url(None)


async def test_numeric_spellings_are_resolved_like_any_name() -> None:
    """2852039166 is 169.254.169.254 to the resolver, as it is to the connection."""
    with pytest.raises(outbound.RefusedAddressError):
        await outbound.check_url("http://2852039166/latest/meta-data/")
    with pytest.raises(outbound.RefusedAddressError):
        await outbound.check_url("http://0/")


# ── the API ──────────────────────────────────────────────────────────────


async def _user_with_role(dbsession: AsyncSession, role: str) -> User:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    user = User(
        email=f"{role}-{uuid.uuid4().hex}@test.local",
        hashed_password="x",  # noqa: S106 - no sign-in in these tests
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(user)
    await dbsession.flush()
    await rbac.assign_role(user.id, (await rbac.get_role_by_name(role)).id)
    return user


@pytest.fixture(autouse=True)
def _service(fastapi_app: FastAPI) -> None:
    service = LLMService(SimpleNamespace(), gateway_sync=GatewaySyncService(None))  # type: ignore[arg-type]
    fastapi_app.dependency_overrides[get_llm_service] = lambda: service


@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_the_connection_test_is_for_those_who_may_add_providers(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, role: str,
) -> None:
    user = await _user_with_role(dbsession, role)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    resp = await client.post(
        "/api/llm/providers/test-endpoint", json={"endpoint_url": "http://10.0.0.5:8000/v1"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Permission denied: llm.providers:create"


async def test_the_connection_test_does_not_call_metadata(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _user_with_role(dbsession, "admin")
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    called: list[str] = []
    real_client = httpx.AsyncClient

    def _models(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "qwen"}]})

    monkeypatch.setattr(
        "faden_backend.web.api.llm.providers.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_models), **{k: v for k, v in kw.items() if k != "verify"}),
    )

    resp = await client.post(
        "/api/llm/providers/test-endpoint", json={"endpoint_url": "http://169.254.169.254/latest"},
    )
    assert resp.status_code == 200
    assert resp.json()["compatible"] is False
    assert "metadata" in resp.json()["error"]
    assert called == [], "nothing was asked"

    resp = await client.post(
        "/api/llm/providers/test-endpoint", json={"endpoint_url": "http://10.0.0.5:8000/v1"},
    )
    assert resp.json() == {"compatible": True, "models": ["qwen"], "error": None}
    assert called == ["http://10.0.0.5:8000/v1/models"], "a LAN model server is asked as before"


async def test_an_endpoint_at_a_metadata_or_link_local_address_is_not_saved(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    admin = await _user_with_role(dbsession, "admin")
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    resp = await client.post(
        "/api/llm/providers/",
        json={
            "name": f"meta-{uuid.uuid4().hex[:6]}",
            "type": "vllm",
            "target": "remote_endpoint",
            "endpoint_url": "http://169.254.169.254/v1",
            "remote_model": "x",
        },
    )
    assert resp.status_code == 422, resp.text
    assert "metadata" in resp.json()["detail"]

    provider = await ProviderDAO(dbsession).create(
        name=f"lan-{uuid.uuid4().hex[:6]}",
        type_=ProviderType.VLLM,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="http://10.0.0.5:8000",
    )
    await dbsession.flush()
    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"endpoint_url": "http://[fe80::1]:8000/v1"})
    assert resp.status_code == 422, resp.text
    assert "link-local" in resp.json()["detail"]
    await dbsession.refresh(provider)
    assert provider.endpoint_url == "http://10.0.0.5:8000", "left as it was"

    resp = await client.patch(
        f"/api/llm/providers/{provider.id}", json={"endpoint_url": "http://host.docker.internal:11434/v1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["endpoint_url"] == "http://host.docker.internal:11434"


# ── the health probes ───────────────────────────────────────────────────


async def test_the_health_probes_do_not_ask_metadata() -> None:
    asked: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        refused = await remote_health.probe("http://169.254.169.254/latest/meta-data/", client)
        assert not refused.reachable and "metadata" in refused.detail
        assert (await remote_health.probe("http://10.0.0.5:8000", client)).reachable
    assert asked == ["http://10.0.0.5:8000"]

    runtime = LLMRuntime(name="r", endpoint_url="http://[fe80::1]:8000")
    health = await CloudAdapter().get_health(runtime)
    assert not health.healthy and "link-local" in (health.detail or "")
