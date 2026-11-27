"""A long chat's summary: the administrator's switch per model, and the console's view of it (0.7.2 K).

Settings -> Chat -> Summaries of long chats (``chat.summary_models``) names the models that write one; none do by
default. The gateway reads it live. The console opens a chat's summary through the chat proxy, as the person.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.services.chat.gateway_client import GatewayChatClient
from faden_backend.services.system_settings.registry import (
    SystemApplyScope,
    check_model_names,
    registry_by_key,
    validate_value,
)
from faden_backend.web.api.chat import views as chat_views

SESSION = "33333333-3333-3333-3333-333333333333"


def test_no_model_writes_a_summary_by_default_and_the_gateway_follows_it_live() -> None:
    defn = registry_by_key()["chat.summary_models"]
    assert defn.default == []
    assert (defn.type, defn.category, defn.group) == ("json", "chat", "summaries")
    assert defn.apply_scope == SystemApplyScope.LIVE_RELOAD and defn.service_targets == ("faden-gateway",)
    assert not defn.is_secret


@pytest.mark.parametrize("value", [[], ["qwen3-32b"], ["a" * 256, "chat-b"], ["m"] * 200])
def test_a_list_of_model_names_is_taken(value: list[str]) -> None:
    check_model_names(value)
    assert validate_value(registry_by_key()["chat.summary_models"], value) == value


@pytest.mark.parametrize("value", [
    "qwen3-32b", {"qwen3-32b": True}, [""], ["a" * 257], [7], [None], ["m"] * 201, None,
])
def test_anything_else_is_refused(value: object) -> None:
    with pytest.raises(ValueError, match="model names"):
        check_model_names(value)


@pytest.mark.anyio
async def test_the_console_opens_the_chats_summary_as_the_person(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, str | None]] = []
    summary = {"data": {"summary_text": "Goal and constraints: a move.", "model_alias": "qwen3-32b",
                        "record_id": "rec-1"}}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == f"/v1/sessions/{SESSION}/summary":
            return httpx.Response(200, json=summary)
        return httpx.Response(404, json={"error": {"message": "Session not found.", "code": "session_not_found"}})

    gateway = GatewayChatClient(base_url="http://gateway.local")
    gateway._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://gateway.local", transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(chat_views, "_client", lambda: gateway)
    client.cookies.set("fapiauth", "jwt")

    found = await client.get(f"/api/chat/sessions/{SESSION}/summary")
    other = await client.get("/api/chat/sessions/44444444-4444-4444-4444-444444444444/summary")
    not_an_id = await client.get("/api/chat/sessions/..%2F..%2Fmodels/summary")

    assert found.status_code == 200 and found.json() == summary
    assert other.status_code == 404
    assert not_an_id.status_code in (404, 422), "only a chat's id reaches the gateway"
    assert asked == [
        (f"/v1/sessions/{SESSION}/summary", "Bearer jwt"),
        ("/v1/sessions/44444444-4444-4444-4444-444444444444/summary", "Bearer jwt"),
    ]
