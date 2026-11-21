"""The console reaches a project's Files through the chat proxy (0.7.2 J).

The gateway had the routes, the console's proxy did not: the project settings' Files section could not load, add or
remove a file. The proxy forwards to the gateway's project routes, keeps the gateway's status (a 404 stays a 404:
the file is gone) and its error body.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.services.chat.gateway_client import GatewayChatClient
from faden_backend.web.api.chat import views as chat_views

pytestmark = pytest.mark.anyio

PROJECT = "11111111-1111-1111-1111-111111111111"
FILE = "22222222-2222-2222-2222-222222222222"


class _Gateway:
    """The gateway as the proxy sees it: what it was asked, and its answers."""

    def __init__(self, answers: dict[tuple[str, str], httpx.Response]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, str, dict[str, str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.asked.append((request.method, request.url.path, dict(request.headers)))
        return self.answers.get((request.method, request.url.path), httpx.Response(404, json={"error": {}}))


def _proxy(monkeypatch: pytest.MonkeyPatch, gateway: _Gateway) -> None:
    client = GatewayChatClient(base_url="http://gateway.local")
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://gateway.local", transport=httpx.MockTransport(gateway.handler),
    )
    monkeypatch.setattr(chat_views, "_client", lambda: client)


async def test_the_files_are_listed_added_and_removed_through_the_project_routes(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = f"/v1/sessions/projects/{PROJECT}/attachments"
    gateway = _Gateway({
        ("GET", base): httpx.Response(200, json={"data": [{"id": FILE, "filename": "plan.pdf"}]}),
        ("POST", base): httpx.Response(201, json={"attachment": {"id": FILE}, "extracted_text_length": 3,
                                                  "token_estimate": 1}),
        ("DELETE", f"{base}/{FILE}"): httpx.Response(204),
    })
    _proxy(monkeypatch, gateway)
    client.cookies.set("fapiauth", "jwt")

    listed = await client.get(f"/api/chat/projects/{PROJECT}/attachments")
    added = await client.post(f"/api/chat/projects/{PROJECT}/attachments",
                              files={"file": ("plan.pdf", b"%PDF-1.4", "application/pdf")})
    removed = await client.delete(f"/api/chat/projects/{PROJECT}/attachments/{FILE}")

    assert listed.status_code == 200 and listed.json()["data"][0]["filename"] == "plan.pdf"
    assert added.status_code == 201 and added.json()["attachment"]["id"] == FILE
    assert removed.status_code == 204
    assert [(m, p) for m, p, _ in gateway.asked] == [("GET", base), ("POST", base), ("DELETE", f"{base}/{FILE}")]
    assert all(h.get("authorization") == "Bearer jwt" for _, _, h in gateway.asked), "as the person"


async def test_a_file_already_gone_stays_a_404_and_a_refusal_keeps_its_message(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = f"/v1/sessions/projects/{PROJECT}/attachments"
    refusal = {"error": {"message": "This file type is not taken.", "code": "attachment_error"}}
    gateway = _Gateway({
        ("DELETE", f"{base}/{FILE}"): httpx.Response(404, json={"error": {"message": "Attachment not found"}}),
        ("POST", base): httpx.Response(415, json=refusal),
    })
    _proxy(monkeypatch, gateway)
    client.cookies.set("fapiauth", "jwt")

    gone = await client.delete(f"/api/chat/projects/{PROJECT}/attachments/{FILE}")
    refused = await client.post(f"/api/chat/projects/{PROJECT}/attachments",
                                files={"file": ("x.exe", b"MZ", "application/octet-stream")})

    assert gone.status_code == 404
    assert refused.status_code == 415 and refused.json() == refusal


def test_the_client_names_the_gateways_project_routes() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(204 if request.method == "DELETE" else 200, json={} if request.method != "DELETE" else None)

    client = GatewayChatClient(base_url="http://gateway.local")
    client._client = httpx.AsyncClient(base_url="http://gateway.local", transport=httpx.MockTransport(handler))  # noqa: SLF001

    import asyncio

    async def run() -> Any:
        await client.list_project_attachments(PROJECT, "jwt")
        await client.upload_project_attachment(PROJECT, b"x", "a.txt", "text/plain", "jwt")
        await client.delete_project_attachment(PROJECT, FILE, "jwt")

    asyncio.run(run())
    assert seen == [
        f"GET /v1/sessions/projects/{PROJECT}/attachments",
        f"POST /v1/sessions/projects/{PROJECT}/attachments",
        f"DELETE /v1/sessions/projects/{PROJECT}/attachments/{FILE}",
    ]
    assert json.dumps(seen)
