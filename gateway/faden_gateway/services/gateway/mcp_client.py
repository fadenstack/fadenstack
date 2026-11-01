"""HTTP client for the MCP micro-service.

Used by the gateway pipeline to fetch the MCP tool catalog and
execute tool calls via the MCP service's internal API.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MCPUnavailableError(Exception):
    """The MCP service could not answer; *reached*: it answered with an error status."""

    def __init__(self, *, reached: bool) -> None:
        super().__init__("MCP service unavailable")
        self.reached = reached


@dataclass(slots=True)
class ToolCatalog:
    """Snapshot of the MCP tool catalog for a tenant."""

    version: int
    tools: list[dict[str, Any]]


def _catalog_version(data: dict[str, Any]) -> int:
    """The catalog version the MCP service sends, as ``catalog_version``.

    It was read as ``version``, always 0 -- and the tool cache takes 0 for
    "unknown" and fetches the whole catalog, so every chat turn did.
    """
    return int(data.get("catalog_version", data.get("version", 0)) or 0)


def _content_text(content: Any) -> str:
    """An MCP result's content as the model reads it: the text of its parts.

    It went to the model as ``str()`` of the parts list --
    "[{'type': 'text', 'text': 'The sum of 17 and 25 is 42.'}]" -- a
    Python repr, twice the tokens, and not what the tool said.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else json.dumps(content)
    return "\n".join(_part_text(part) for part in content)


def _part_text(part: Any) -> str:
    """A content part's text, or the part itself as JSON when it is not text."""
    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return json.dumps(part)


def _reason(response: httpx.Response) -> str:
    """The MCP service's ``detail``, or the status text."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) and detail else response.reason_phrase


def _failure_for_model(qualified_name: str, status: int, reason: str) -> str:
    """What the model is told, so it can do better than apologise.

    It was told "MCP tool call failed: 404" -- for a tool it had made up
    (release VM, 2026-09-25) -- and could not tell that from an outage.
    """
    if status == 404 and reason == "Tool not found.":
        return (
            f"There is no tool named '{qualified_name}'. "
            "Call only the tools you were given."
        )
    return f"Tool '{qualified_name}' failed: {reason}"


@dataclass(slots=True)
class ToolCallResult:
    """Result of executing an MCP tool call."""

    content: str
    is_error: bool = False
    redaction_summary: dict[str, Any] | None = None


class MCPClient:
    """Async client wrapping MCP service internal HTTP endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        self._headers = {"Authorization": f"Bearer {service_token}"}

    async def get_tool_catalog(self, tenant_id: str) -> ToolCatalog:
        """Fetch the full tool catalog for a tenant.

        Calls ``GET /api/internal/tools/catalog?tenant_id=<tid>``.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/internal/tools/catalog",
                params={"tenant_id": tenant_id},
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return ToolCatalog(
                version=_catalog_version(data),
                tools=data.get("tools", []),
            )
        except Exception:
            logger.exception("MCP catalog fetch failed")
            return ToolCatalog(version=0, tools=[])

    async def list_tools(self, tenant_id: str) -> list[dict[str, Any]]:
        """Every tool of the tenant, switched off or not, for a list a person reads.

        Calls ``GET /api/internal/tools/listing?tenant_id=<tid>``. Unlike the catalog it
        raises ``MCPUnavailableError`` when the service cannot answer, so the caller can say so.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/internal/tools/listing",
                params={"tenant_id": tenant_id},
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            tools = resp.json().get("tools", [])
        except httpx.HTTPStatusError as exc:
            raise MCPUnavailableError(reached=True) from exc
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise MCPUnavailableError(reached=False) from exc
        return tools if isinstance(tools, list) else []

    async def get_catalog_version(self, tenant_id: str) -> int:
        """Fetch just the catalog version for cache validation.

        Calls ``GET /api/internal/tools/catalog/version?tenant_id=<tid>``.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/internal/tools/catalog/version",
                params={"tenant_id": tenant_id},
                headers=self._headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            return _catalog_version(resp.json())
        except Exception:
            logger.debug("MCP catalog version check failed", exc_info=True)
            return 0

    async def call_tool(
        self,
        *,
        qualified_name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        request_id: str,
        pii_mode_override: str | None = None,
    ) -> ToolCallResult:
        """Execute an MCP tool call via the MCP service.

        Calls ``POST /api/internal/tools/call``.
        """
        body: dict[str, Any] = {
            "qualified_name": qualified_name,
            "arguments": arguments,
            "tenant_id": tenant_id,
            "request_id": request_id,
        }
        if pii_mode_override is not None:
            body["pii_mode_override"] = pii_mode_override
        try:
            resp = await self._http.post(
                f"{self._base}/api/internal/tools/call",
                json=body,
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return ToolCallResult(
                content=_content_text(data.get("content")),
                is_error=data.get("is_error", False),
                redaction_summary=data.get("redaction_summary"),
            )
        except httpx.HTTPStatusError as exc:
            reason = _reason(exc.response)
            logger.warning(
                "MCP tool call failed: %s %s %s",
                qualified_name,
                exc.response.status_code,
                reason,
            )
            return ToolCallResult(
                content=_failure_for_model(qualified_name, exc.response.status_code, reason),
                is_error=True,
            )
        except Exception:
            logger.exception("MCP tool call failed: %s", qualified_name)
            return ToolCallResult(
                content="MCP tool call failed: service unavailable",
                is_error=True,
            )
