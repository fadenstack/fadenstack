"""MCP server URLs: no link-local or metadata address; a URL joins only an MCP or own container.

The MCP service fetches a registered server's URL and hands its settings
answer to the console: a URL ending in ``?`` read any JSON an internal address
served, a cloud's instance metadata among them. The discovery scan put
whatever host it was given into its URL (``10.0.0.5:6379#``). And naming a
container in a URL joined it to the MCP service's network: any container,
without Root Mode. LAN, loopback and container names stay allowed: that is
where MCP servers run.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from faden_backend.db.models.containers import ContainerClass, ContainerPolicy
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import outbound
from faden_backend.services.mcp.client import get_mcp_client
from faden_backend.web.api.admin.containers import platform_volumes
from faden_backend.web.api.admin.dependencies import get_docker
from faden_backend.web.api.admin.mcp import views

pytestmark = pytest.mark.anyio

ANSWERS = {
    "metadata.google.internal": ["169.254.169.254"],
    "mcp-github": ["172.18.0.9"],
}
NETWORK = "fadenstack_default"


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, **_: object) -> list[str]:
        return ANSWERS.get(host, [])

    monkeypatch.setattr(outbound, "resolve", resolve)
    monkeypatch.setattr(platform_volumes, "own_project", AsyncMock(return_value="fadenstack"))


def _container(name: str, cid: str, *, networks: dict[str, Any] | None = None, project: str | None = None) -> dict:
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Config": {"Labels": {"com.docker.compose.project": project} if project else {}},
        "NetworkSettings": {"Networks": networks or {"bridge": {}}},
    }


@pytest.fixture()
def docker() -> MagicMock:
    """Docker with the MCP service on the platform network, and the containers a test adds."""
    mock = MagicMock()
    mock.containers = {
        "faden-mcp": _container("faden-mcp", "m" * 64, networks={NETWORK: {}}, project="fadenstack"),
    }
    mock.find_container_by_name = AsyncMock(side_effect=mock.containers.get)
    mock.list_networks = AsyncMock(return_value=[{"Name": NETWORK, "Id": "net-1"}])
    mock.connect_container_to_network = AsyncMock()
    return mock


@pytest.fixture()
def mcp_client() -> MagicMock:
    client = MagicMock()
    client.register_server = AsyncMock(return_value={"id": "s1"})
    client.update_server = AsyncMock(return_value={"id": "s1"})
    client.list_servers = AsyncMock(return_value={"servers": [], "total": 0})
    return client


@pytest.fixture()
def admin(fastapi_app: FastAPI, docker: MagicMock, mcp_client: MagicMock) -> FastAPI:
    user = User(
        id=uuid.uuid4(), email=f"a-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_superuser=True, is_verified=True,
    )
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[get_docker] = lambda: docker
    fastapi_app.dependency_overrides[get_mcp_client] = lambda: mcp_client
    return fastapi_app


# ── Registering and changing a server ────────────────────────────────


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("http://169.254.169.254/latest/meta-data/?", "cloud metadata"),
        ("http://metadata.google.internal/computeMetadata/v1/#", "metadata.google.internal (169.254.169.254)"),
        ("http://[fe80::1]:8080/mcp", "link-local"),
        ("http://2852039166/", "cloud metadata"),
    ],
)
async def test_a_server_at_a_metadata_or_link_local_address_is_refused(
    client: AsyncClient, admin: FastAPI, docker: MagicMock, mcp_client: MagicMock, url: str, says: str,
) -> None:
    resp = await client.post(
        "/api/admin/mcp/servers",
        json={"name": "x", "transport": "streamable_http", "url": url, "tool_prefix": "x"},
    )
    assert resp.status_code == 422, resp.text
    assert says in resp.json()["detail"]

    resp = await client.patch("/api/admin/mcp/servers/s1", json={"url": url})
    assert resp.status_code == 422, resp.text

    mcp_client.register_server.assert_not_called()
    mcp_client.update_server.assert_not_called()
    docker.find_container_by_name.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp-github:8080/mcp",
        "http://10.0.0.5:8080/mcp",
        "http://127.0.0.1:3001/mcp",
        "http://host.docker.internal:3001/sse",
        "https://api.githubcopilot.com/mcp",
    ],
)
async def test_where_mcp_servers_run_is_registered(
    client: AsyncClient, admin: FastAPI, mcp_client: MagicMock, url: str,
) -> None:
    payload = {"name": "x", "transport": "streamable_http", "url": url, "tool_prefix": "x"}
    resp = await client.post("/api/admin/mcp/servers", json=payload)
    assert resp.status_code == 201, resp.text
    mcp_client.register_server.assert_awaited_once_with(payload)

    resp = await client.patch("/api/admin/mcp/servers/s1", json={"url": url})
    assert resp.status_code == 200, resp.text


async def test_a_stdio_server_has_no_url_to_check(
    client: AsyncClient, admin: FastAPI, mcp_client: MagicMock,
) -> None:
    payload = {"name": "t", "transport": "stdio", "command": ["uvx", "mcp-server-time"], "tool_prefix": "t"}
    resp = await client.post("/api/admin/mcp/servers", json=payload)
    assert resp.status_code == 201, resp.text


# ── The discovery scan ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    ["10.0.0.5:6379#", "user@10.0.0.5", "mcp github", "[::1]", "-mcp", "a" * 254],
)
async def test_the_scan_takes_a_host_name_or_an_ipv4_address(
    client: AsyncClient, admin: FastAPI, monkeypatch: pytest.MonkeyPatch, host: str,
) -> None:
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(views, "_probe_mcp_port", probe)
    resp = await client.post("/api/admin/mcp/scan", json={"host": host, "port_start": 8000, "port_end": 8001})
    assert resp.status_code == 422, resp.text
    probe.assert_not_called()


async def test_the_scan_does_not_probe_a_metadata_address(
    client: AsyncClient, admin: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(views, "_probe_mcp_port", probe)
    for host in ("169.254.169.254", "metadata.google.internal"):
        resp = await client.post("/api/admin/mcp/scan", json={"host": host, "port_start": 80, "port_end": 80})
        assert resp.status_code == 422, resp.text
        assert "metadata" in resp.json()["detail"]
    probe.assert_not_called()


@pytest.mark.parametrize(
    ("host", "probed"),
    [
        ("mcp-github", "mcp-github"),
        ("http://mcp-github:8080/mcp", "mcp-github"),
        ("host.docker.internel", "host.docker.internal"),
        ("192.168.1.20", "192.168.1.20"),
    ],
)
async def test_the_scan_probes_the_host_it_was_given(
    client: AsyncClient, admin: FastAPI, monkeypatch: pytest.MonkeyPatch, host: str, probed: str,
) -> None:
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(views, "_probe_mcp_port", probe)
    resp = await client.post("/api/admin/mcp/scan", json={"host": host, "port_start": 8080, "port_end": 8081})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"discovered": [], "scanned_ports": 2}
    assert sorted(call.args for call in probe.await_args_list) == [(probed, 8080), (probed, 8081)]


# ── Which containers a URL joins to the MCP service's network ────────


async def _classed(
    dbsession: AsyncSession, cid: str, name: str, cls: ContainerClass, policy: ContainerPolicy = ContainerPolicy.FREE,
) -> None:
    await ContainerRegistryDAO(dbsession).upsert(container_id=cid, name=name, container_class=cls, policy=policy)


async def test_an_mcp_or_own_container_is_joined(docker: MagicMock, dbsession: AsyncSession) -> None:
    registry = ContainerRegistryDAO(dbsession)
    docker.containers["mcp-github"] = _container("mcp-github", "1" * 64)  # MCP by its name
    docker.containers["my-tools"] = _container("my-tools", "2" * 64, project="tools")
    await _classed(dbsession, "2" * 64, "my-tools", ContainerClass.TENANT_APP)

    await views._ensure_container_network(docker, registry, "http://mcp-github:8080/mcp")
    await views._ensure_container_network(docker, registry, "http://my-tools:3001/sse")
    assert [c.args for c in docker.connect_container_to_network.await_args_list] == [
        ("net-1", "1" * 64), ("net-1", "2" * 64),
    ]


async def test_any_other_container_is_left_where_it_is(docker: MagicMock, dbsession: AsyncSession) -> None:
    registry = ContainerRegistryDAO(dbsession)
    docker.containers["other-app-db"] = _container("other-app-db", "3" * 64, project="other-app")  # unclassified
    docker.containers["mcp-locked"] = _container("mcp-locked", "4" * 64)
    await _classed(dbsession, "4" * 64, "mcp-locked", ContainerClass.MCP, ContainerPolicy.LOCKED)
    docker.containers["faden-postgres"] = _container("faden-postgres", "5" * 64, project="other")  # SYSTEM_CORE
    docker.containers["mcp-ours"] = _container("mcp-ours", "6" * 64, project="fadenstack")  # Fadenstack's own
    await _classed(dbsession, "6" * 64, "mcp-ours", ContainerClass.MCP)

    for name in ("other-app-db", "mcp-locked", "faden-postgres", "mcp-ours", "not-there"):
        await views._ensure_container_network(docker, registry, f"http://{name}:5432/")
    docker.connect_container_to_network.assert_not_called()


async def test_a_registered_url_joins_its_mcp_container(
    client: AsyncClient, admin: FastAPI, docker: MagicMock, mcp_client: MagicMock,
) -> None:
    docker.containers["mcp-github"] = _container("mcp-github", "1" * 64)
    docker.containers["other-app-db"] = _container("other-app-db", "3" * 64)
    for url in ("http://mcp-github:8080/mcp", "http://other-app-db:5432/"):
        resp = await client.post(
            "/api/admin/mcp/servers",
            json={"name": "x", "transport": "streamable_http", "url": url, "tool_prefix": "x"},
        )
        assert resp.status_code == 201, resp.text
    docker.connect_container_to_network.assert_awaited_once_with("net-1", "1" * 64)
