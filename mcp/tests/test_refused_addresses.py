"""The MCP service calls no link-local or cloud metadata address.

The backend refuses such a server URL when it is registered, but the MCP
service fetched whatever URL a row held, and the settings proxy handed the
JSON answer to the console: a URL ending in ``?`` made ``/api/settings`` a
query string, so the console could read the cloud's instance metadata. The
service now checks before it connects and before it fetches settings, by name
too, once resolved. Loopback, private ranges and container names stay allowed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from faden_mcp.services import connection_manager, outbound
from faden_mcp.services.connection_manager import MCPConnectionManager
from faden_mcp.services.transport.base import MCPConnectError
from faden_mcp.web.api.admin import views

ANSWERS = {
    "metadata.google.internal": ["169.254.169.254"],
    "two-faced.lan": ["10.0.0.7", "169.254.169.254"],
    "mcp-github": ["172.18.0.9"],
}
METADATA = "http://169.254.169.254/latest/meta-data?"


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer lookups from ANSWERS; any other name does not resolve."""

    async def resolve(host: str) -> list[str]:
        return ANSWERS.get(host, [])

    monkeypatch.setattr(outbound, "resolve", resolve)


@pytest.mark.parametrize(
    ("url", "says"),
    [
        (METADATA, "cloud metadata"),
        ("http://[fd00:ec2::254]/", "cloud metadata"),
        ("http://100.100.100.200/", "cloud metadata"),
        ("http://[::ffff:169.254.169.254]/", "cloud metadata"),
        ("http://2852039166/", "cloud metadata"),
        ("http://169.254.10.1:8080/mcp", "link-local"),
        ("http://[fe80::1]:8080/mcp", "link-local"),
        ("http://0.0.0.0:8080/mcp", "unspecified"),
        ("http://metadata.google.internal/v1/#", "(169.254.169.254)"),
        ("http://two-faced.lan:8080/mcp", "two-faced.lan (169.254.169.254)"),
    ],
)
@pytest.mark.asyncio
async def test_link_local_and_metadata_addresses_are_refused(
    url: str, says: str,
) -> None:
    """By address, in old spellings, and by a name that resolves to one."""
    reason = await outbound.refusal(url)
    assert reason is not None and says in reason


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp-github:8080/mcp",
        "http://127.0.0.1:3001/mcp",
        "http://[::1]:3001/mcp",
        "http://10.0.0.5:8080/mcp",
        "http://192.168.1.20/sse",
        "https://api.githubcopilot.com/mcp",
        "http://unresolvable.lan/mcp",  # fails by itself, later
        None,
    ],
)
@pytest.mark.asyncio
async def test_where_mcp_servers_run_is_allowed(url: str | None) -> None:
    """Containers, loopback, the LAN and the internet."""
    assert await outbound.refusal(url) is None


def _server(
    transport: str, url: str | None, command: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="s",
        transport=transport,
        url=url,
        command_json=command,
        args_json=[],
        working_dir=None,
        headers_json_encrypted=None,
        env_json_encrypted=None,
        timeout_sec=10,
        tool_prefix="s",
    )


class _Adapter:
    def __init__(self, config: object) -> None:
        self.config = config
        self.connected = False

    async def connect(self) -> None:
        self.connected = True


@pytest.mark.asyncio
async def test_a_server_at_a_metadata_address_is_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused before an adapter is made; an HTTP server elsewhere and stdio connect."""
    made: list[_Adapter] = []

    def create(config: object) -> _Adapter:
        made.append(_Adapter(config))
        return made[-1]

    monkeypatch.setattr(connection_manager, "_create_adapter", create)
    manager = MCPConnectionManager()

    refused = _server("streamable_http", "http://metadata.google.internal/mcp")
    with pytest.raises(MCPConnectError, match="cloud metadata"):
        await manager.start(refused)  # type: ignore[arg-type]
    assert made == [] and manager.active_count == 0

    await manager.start(_server("sse", "http://mcp-github:8080/sse"))  # type: ignore[arg-type]
    stdio = _server("stdio", None, ["uvx", "mcp-server-time"])
    await manager.start(stdio)  # type: ignore[arg-type]
    assert [a.connected for a in made] == [True, True]


def test_the_service_has_no_port_scanner() -> None:
    """``/scan`` probed any host and port; nothing called it.

    The console scans through the backend's own.
    """
    paths = [getattr(route, "path", "") for route in views.router.routes]
    assert not [path for path in paths if path.endswith("/scan")]


@pytest.mark.asyncio
async def test_the_settings_proxy_does_not_fetch_from_a_metadata_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is asked of it; a server in a container is asked as before."""
    asked: list[str] = []
    real = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"theme": "dark"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    fetch = views._fetch_from_server  # noqa: SLF001

    with pytest.raises(MCPConnectError, match="cloud metadata"):
        await fetch(METADATA, "")
    assert asked == []

    assert await fetch("http://mcp-github:8080", "/schema") == {"theme": "dark"}
    assert asked == ["http://mcp-github:8080/api/settings/schema"]


@pytest.mark.parametrize(
    ("url", "asks"),
    [
        ("http://mcp-github:8080/mcp/", "http://mcp-github:8080/mcp/api/settings"),
        ("http://mcp-github:8080/admin/keys.json?", "http://mcp-github:8080/admin/keys.json/api/settings"),
        ("http://mcp-github:8080/admin/keys.json#", "http://mcp-github:8080/admin/keys.json/api/settings"),
        ("http://mcp-github:8080/mcp?token=x", "http://mcp-github:8080/mcp/api/settings"),
    ],
)
@pytest.mark.asyncio
async def test_the_settings_are_asked_for_below_the_servers_path(
    monkeypatch: pytest.MonkeyPatch, url: str, asks: str,
) -> None:
    """A ``?`` or ``#`` at the end no longer turns ``/api/settings`` into a query."""
    asked: list[str] = []
    real = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    await views._fetch_from_server(url, "")  # noqa: SLF001
    assert asked == [asks]
