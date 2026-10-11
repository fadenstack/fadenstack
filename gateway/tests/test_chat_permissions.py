"""The chat is for those whose roles include it: the ``chat.*`` permissions are checked.

Any active account could chat, whatever its roles. Now each chat route asks for its permission
(``default_user`` has them all); a superuser may do everything, and the rest of the API (models,
embeddings, ...) is not the chat.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_gateway.services.gateway.agents import AgentDirectory, AgentLookupError, UserFlags
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context

pytestmark = pytest.mark.anyio

DEFAULT_USER = frozenset({
    "chat.projects:create", "chat.projects:read", "chat.projects:update", "chat.projects:delete",
    "chat.sessions:create", "chat.sessions:read", "chat.sessions:update", "chat.sessions:delete",
    "chat.attachments:create", "chat.attachments:read", "chat.attachments:delete",
    "chat.tool_policy:read", "chat.tool_policy:update",
})
USERS = {
    "chatter": UserFlags(is_active=True, is_superuser=False, chat_permissions=DEFAULT_USER),
    "viewer": UserFlags(is_active=True, is_superuser=False),
    "reader": UserFlags(is_active=True, is_superuser=False, chat_permissions=frozenset({"chat.sessions:read"})),
    "root": UserFlags(is_active=True, is_superuser=True),
}


class _Backend:
    def __init__(self) -> None:
        self.fail = False

    async def user(self, user_id: str) -> UserFlags | None:
        if self.fail:
            raise AgentLookupError("backend database down")
        return USERS.get(user_id)

    async def agent(self, key: tuple[str, str]) -> None:
        return None


@pytest.fixture
def backend(fastapi_app: FastAPI) -> _Backend:
    fake = _Backend()
    fastapi_app.state.agent_directory = AgentDirectory(fetch_agent=fake.agent, fetch_user=fake.user)
    return fake


def _as(fastapi_app: FastAPI, user: str) -> None:
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id=user, tenant_id="t1", raw_claims={},
    )


async def _status(client: AsyncClient, method: str, url: str, **kwargs: Any) -> tuple[int, str | None]:
    r = await client.request(method, url, **kwargs)
    code = r.json().get("error", {}).get("code") if r.status_code >= 400 and r.content else None  # noqa: PLR2004
    return r.status_code, code


SOME_CHAT = "6d2b8f1e-3c4a-4b5d-8e9f-0a1b2c3d4e5f"
CHAT_ROUTES = [
    ("GET", "/v1/sessions", {}),
    ("POST", "/v1/sessions", {"json": {"title": "x"}}),
    ("GET", "/v1/sessions/projects", {}),
    ("POST", "/v1/sessions/projects", {"json": {"name": "x"}}),
    ("GET", "/v1/sessions/attachments/stats", {}),
    ("GET", f"/v1/sessions/{SOME_CHAT}/tool-policy", {}),
    ("PATCH", f"/v1/sessions/{SOME_CHAT}/tool-policy", {"json": {"execution_mode": "server_only"}}),
    ("POST", "/v1/chat/completions", {"json": {"model": "m", "messages": [{"role": "user", "content": "hi"}]}}),
]


async def test_an_account_whose_roles_do_not_chat_cannot(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    _as(fastapi_app, "viewer")
    for method, url, kwargs in CHAT_ROUTES:
        assert await _status(client, method, url, **kwargs) == (403, "permission_denied"), (method, url)


async def test_the_chat_role_and_superusers_chat(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    for user in ("chatter", "root"):
        _as(fastapi_app, user)
        assert (await _status(client, "GET", "/v1/sessions"))[0] == 200, user
        assert (await _status(client, "POST", "/v1/sessions", json={"title": "x"}))[0] == 201, user
        assert (await _status(client, "GET", "/v1/sessions/attachments/stats"))[0] != 403, user


async def test_each_route_asks_for_its_own_permission(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    _as(fastapi_app, "reader")
    assert (await _status(client, "GET", "/v1/sessions"))[0] == 200
    assert await _status(client, "POST", "/v1/sessions", json={"title": "x"}) == (403, "permission_denied")
    assert await _status(client, "GET", "/v1/sessions/projects") == (403, "permission_denied")
    assert await _status(client, *CHAT_ROUTES[-1][:2], **CHAT_ROUTES[-1][2]) == (403, "permission_denied")


async def test_the_rest_of_the_api_is_not_the_chat(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    _as(fastapi_app, "viewer")
    assert (await _status(client, "GET", "/v1/models"))[0] == 200


async def test_a_check_that_could_not_be_made_is_no_pass(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    _as(fastapi_app, "chatter")
    backend.fail = True
    assert await _status(client, "GET", "/v1/sessions") == (503, "access_check_failed")
