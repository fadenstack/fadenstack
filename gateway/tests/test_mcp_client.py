"""What the gateway reads from the MCP service: the catalog version, a result's text."""

from __future__ import annotations

import httpx
import pytest

from faden_gateway.services.gateway.mcp_client import MCPClient

SERVICE_TOKEN = "service-token"


def _client(http: httpx.AsyncClient) -> MCPClient:
    return MCPClient(base_url="http://mcp.local", http_client=http, service_token=SERVICE_TOKEN)


@pytest.mark.anyio
async def test_the_catalog_version_is_read_as_the_service_sends_it() -> None:
    """``catalog_version``: read as ``version`` it was always 0, and nothing was cached."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"catalog_version": 7, "tenant_id": "default"})
        return httpx.Response(200, json={"catalog_version": 7, "tools": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        mcp = _client(http)
        assert await mcp.get_catalog_version("default") == 7
        assert (await mcp.get_tool_catalog("default")).version == 7


@pytest.mark.anyio
async def test_a_result_reaches_the_model_as_its_text() -> None:
    """Not as the Python repr of MCP's content parts."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "The sum of 17 and 25 is 42."},
            {"type": "image", "data": "iVBO", "mimeType": "image/png"},
        ], "is_error": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        result = await _client(http).call_tool(
            qualified_name="mcp.everything.get-sum", arguments={}, tenant_id="default", request_id="r1",
        )
    assert result.content.splitlines()[0] == "The sum of 17 and 25 is 42."
    assert '"type": "image"' in result.content.splitlines()[1]
