"""Async HTTP client for proxying chat requests to the API gateway.

The backend uses cookie-based auth (``fapiauth`` httponly JWT).  This
client converts that to a ``Bearer`` token that the gateway expects and
proxies all chat-related requests, including SSE streams.

Usage::

    client = GatewayChatClient()
    models = await client.list_models(jwt="<token>")
    stream = await client.open_stream_chat(payload, jwt="<token>")
    async for chunk in stream.body:
        ...  # forward SSE bytes (and stream.headers)
"""

from __future__ import annotations

import logging
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from llm_port_backend.settings import settings
from llm_port_backend.services.tls import default_httpx_verify

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 300.0  # 5 min for long generation

#: Gateway response headers the browser needs from a streamed completion: the
#: trace id a reply's info button opens the debug panel with.
FORWARDED_STREAM_HEADERS = ("x-langfuse-trace-id",)


async def _nothing() -> None:
    return None


@dataclass
class GatewayStream:
    """A streamed completion: headers to pass on, then the SSE bytes."""

    headers: dict[str, str]
    body: AsyncIterator[bytes]
    #: Closes the gateway response; safe to call after the body finished.
    aclose: Callable[[], Awaitable[None]] = _nothing


def _sse_error(error: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({'error': error}, ensure_ascii=False)}\n\n".encode()


async def _sse_events(error: dict[str, Any]) -> AsyncIterator[bytes]:
    """An error event and the end of the stream, as the chat client reads them."""
    yield _sse_error(error)
    yield b"data: [DONE]\n\n"


def _unavailable_error() -> dict[str, Any]:
    return {
        "message": "Gateway unavailable",
        "type": "gateway_unavailable",
        "code": "gateway_unavailable",
    }


def _http_error(resp: httpx.Response, body_bytes: bytes) -> dict[str, Any]:
    """The gateway's error, as an SSE error object (its own when it sent one)."""
    body_text = body_bytes.decode("utf-8", errors="replace")
    try:
        detail = json.loads(body_text) if body_text else {}
    except Exception:
        detail = {"error": {"message": body_text or resp.reason_phrase}}
    if not isinstance(detail, dict):
        detail = {"error": {"message": str(detail)}}
    error_obj = dict(detail.get("error") if isinstance(detail.get("error"), dict) else {})
    error_obj.setdefault("message", resp.reason_phrase or "Gateway request failed")
    error_obj.setdefault("type", "gateway_error")
    error_obj.setdefault("code", "gateway_http_error")
    error_obj.setdefault("status", resp.status_code)
    return error_obj

# One client per event loop, shared by every chat request.
#
# Each request used to build its own ``httpx.AsyncClient`` and never close
# it. Building one loads the CA bundle into a fresh SSL context -- ~400 ms on
# the dev workstation, synchronously, on the event loop, even for a plain
# http:// gateway. The chat page makes eight to ten proxy calls per message,
# so every send stalled the whole backend for 3-5 s: the completion waited
# behind them, and everything else the backend serves waited too. Shared, the
# cost is paid once and connections to the gateway are kept alive.
#
# Keyed by loop because a client's connection pool belongs to the loop it was
# first used on; tests run each case on a new one.
_shared: dict[int, GatewayChatClient] = {}


def shared_client() -> GatewayChatClient:
    """The process's gateway client for the running event loop."""
    import asyncio  # noqa: PLC0415

    key = id(asyncio.get_running_loop())
    client = _shared.get(key)
    if client is None:
        client = GatewayChatClient(base_url=settings.gateway_url)
        _shared[key] = client
    return client


async def close_shared_client() -> None:
    """Close the running loop's shared client, on shutdown."""
    import asyncio  # noqa: PLC0415

    client = _shared.pop(id(asyncio.get_running_loop()), None)
    if client is not None:
        await client.close()


class GatewayChatClient:
    """Thin async proxy for the API gateway chat endpoints."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.gateway_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ----------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=default_httpx_verify(),
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT,
                    read=_READ_TIMEOUT,
                    write=_READ_TIMEOUT,
                    pool=_CONNECT_TIMEOUT,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _headers(jwt: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {jwt}"}

    # -- chat completions (streaming) ---------------------------------------

    async def open_stream_chat(
        self,
        payload: dict[str, Any],
        jwt: str,
    ) -> GatewayStream:
        """Start a streamed ``/v1/chat/completions`` on the gateway.

        The gateway's headers are read before its body, so the proxy can pass
        them on: a ``StreamingResponse``'s headers are fixed before its body
        starts, and the old generator only reached the gateway once they
        were. The trace id (``x-langfuse-trace-id``) never got to the browser,
        and a new reply's info button did nothing until the page was
        reloaded and the stored message brought its trace id along.

        Failures do not raise: they come back as an SSE ``error`` event the
        chat client already shows, as before.
        """
        return await self._open_stream(
            "POST", "/v1/chat/completions", jwt, json=payload,
        )

    async def open_stream_resume(self, session_id: str, jwt: str) -> GatewayStream:
        """Reconnect to a session's in-progress (or just finished) stream.

        Like :meth:`open_stream_chat`: the trace id comes back as a header.
        No buffered stream (the gateway's 204) is an empty body.
        """
        return await self._open_stream(
            "GET", f"/v1/sessions/{session_id}/stream", jwt,
        )

    async def _open_stream(
        self,
        method: str,
        url: str,
        jwt: str,
        **kwargs: Any,
    ) -> GatewayStream:
        client = self._ensure_client()
        try:
            request = client.build_request(
                method, url, headers=self._headers(jwt), **kwargs,
            )
            resp = await client.send(request, stream=True)
        except Exception:
            logger.exception("Streaming chat proxy error")
            return GatewayStream({}, _sse_events(_unavailable_error()))

        if resp.is_error:
            try:
                body_bytes = await resp.aread()
            except Exception:
                body_bytes = b""
            finally:
                await resp.aclose()
            return GatewayStream({}, _sse_events(_http_error(resp, body_bytes)))

        headers = {
            name: resp.headers[name]
            for name in FORWARDED_STREAM_HEADERS
            if name in resp.headers
        }

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except Exception:
                logger.exception("Streaming chat proxy error")
                async for event in _sse_events(_unavailable_error()):
                    yield event
            finally:
                await resp.aclose()

        return GatewayStream(headers, body(), resp.aclose)

    # -- chat completions (non-streaming) -----------------------------------

    async def chat(
        self,
        payload: dict[str, Any],
        jwt: str,
    ) -> httpx.Response:
        """Non-streaming chat completions."""
        client = self._ensure_client()
        resp = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers=self._headers(jwt),
        )
        return resp

    # -- stream reconnection ------------------------------------------------

    async def stream_status(self, session_id: str, jwt: str) -> Any:
        """Check if a stream is active for *session_id*."""
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/stream/status",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    # -- models -------------------------------------------------------------

    async def list_models(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get("/v1/models", headers=self._headers(jwt))
        resp.raise_for_status()
        return resp.json()

    # -- capacity -----------------------------------------------------------

    async def get_capacity(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/sessions/capacity", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    # -- projects -----------------------------------------------------------

    async def list_projects(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/sessions/projects", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def create_project(self, body: dict[str, Any], jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            "/v1/sessions/projects", json=body, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_project(self, project_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/projects/{project_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def update_project(
        self, project_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/projects/{project_id}",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_project(self, project_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/projects/{project_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- sessions -----------------------------------------------------------

    async def list_sessions(
        self, jwt: str, *, project_id: str | None = None,
    ) -> Any:
        client = self._ensure_client()
        params: dict[str, str] = {}
        if project_id:
            params["project_id"] = project_id
        resp = await client.get(
            "/v1/sessions", params=params, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def create_session(self, body: dict[str, Any], jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            "/v1/sessions", json=body, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def update_session(
        self, session_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/{session_id}",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{session_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- messages -----------------------------------------------------------

    async def list_messages(
        self, session_id: str, jwt: str, *, limit: int = 100,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/messages",
            params={"limit": limit},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    # -- attachments --------------------------------------------------------

    async def upload_attachment(
        self,
        session_id: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            f"/v1/sessions/{session_id}/attachments",
            files={"file": (filename, file_bytes, content_type)},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def list_attachments(self, session_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/attachments",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_attachment(self, attachment_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{attachment_id}/attachments/{attachment_id}",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- tool availability / policy -----------------------------------------

    async def get_tool_catalog(
        self,
        jwt: str,
        *,
        execution_mode: str = "server_only",
    ) -> Any:
        """Fetch the global tool catalog (no session required)."""
        client = self._ensure_client()
        resp = await client.get(
            "/v1/tools/catalog",
            params={"execution_mode": execution_mode},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_available_tools(
        self,
        session_id: str,
        jwt: str,
        *,
        include_disabled: bool = True,
        include_unavailable: bool = True,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/tools/available",
            params={
                "session_id": session_id,
                "include_disabled": str(include_disabled).lower(),
                "include_unavailable": str(include_unavailable).lower(),
            },
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session_tool_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/tool-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def patch_session_tool_policy(
        self, session_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/{session_id}/tool-policy",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pii_defaults(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/pii-defaults",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session_pii_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/pii-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def patch_session_pii_policy(
        self, session_id: str, body: dict[str, Any], jwt: str,
        *, allow_weaken: bool = False,
    ) -> Any:
        client = self._ensure_client()
        headers = self._headers(jwt)
        if allow_weaken:
            headers["X-PII-Allow-Weaken"] = "true"
        resp = await client.patch(
            f"/v1/sessions/{session_id}/pii-policy",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_session_pii_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{session_id}/pii-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        return resp.json()
