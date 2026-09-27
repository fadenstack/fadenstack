"""``/api/skills``: forwarded as the signed-in user, drafted from a chat.

No database: the user, RBAC and the skills service are stand-ins.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from faden_backend.db.dao.rbac_dao import _DEFAULT_ROLES, RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.skills import drafting
from faden_backend.services.skills.client import SkillsPrincipal, SkillsServiceClient, get_skills_client
from faden_backend.web.api.skills import views as skills_views
from faden_backend.web.api.skills.views import skills_principal, user_caps
from faden_backend.web.application import get_app

PRINCIPAL = SkillsPrincipal(user_id="u-1", group_ids=("g-1", "g-2"), caps=("create", "share"))


def _no_db() -> MagicMock:
    return MagicMock()


def _user(*, superuser: bool = False) -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = superuser
    user.is_verified = True
    return user


class _Upstream:
    """The skills service: records what it is sent and answers from a table."""

    def __init__(self, answer: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = answer or httpx.Response(200, json={"items": [], "total": 0})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream()


@pytest.fixture
def app(upstream: _Upstream) -> FastAPI:
    application = get_app()
    application.dependency_overrides[get_db_session] = _no_db
    application.dependency_overrides[current_active_user] = lambda: _user(superuser=True)
    application.dependency_overrides[skills_principal] = lambda: PRINCIPAL
    application.dependency_overrides[get_skills_client] = lambda: SkillsServiceClient(
        "http://skills.local",
        "svc-token",
        transport=httpx.MockTransport(upstream),
    )
    return application


async def _call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        return await client.request(method, url, cookies={"fapiauth": "jwt"}, **kwargs)


@pytest.mark.anyio
async def test_a_request_goes_to_the_service_as_the_signed_in_user(
    app: FastAPI,
    upstream: _Upstream,
) -> None:
    """Every call used to reach the service as "service": no owner, no sharing."""
    response = await _call(app, "GET", "/api/skills?view=mine&usable=true")
    assert response.status_code == 200
    [sent] = upstream.requests
    assert sent.url.path == "/api/skills"
    assert dict(sent.url.params) == {"view": "mine", "usable": "true"}
    assert sent.headers["authorization"] == "Bearer svc-token"
    assert sent.headers["x-faden-user"] == "u-1"
    assert sent.headers["x-faden-groups"] == "g-1,g-2"
    assert sent.headers["x-faden-caps"] == "create,share"


@pytest.mark.anyio
async def test_the_service_answer_comes_back_as_it_is(upstream: _Upstream, app: FastAPI) -> None:
    upstream.answer = httpx.Response(
        200,
        content=b"PK\x03\x04zip",
        headers={
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="tone.zip"',
        },
    )
    response = await _call(app, "GET", f"/api/skills/{uuid.uuid4()}/export")
    assert response.content == b"PK\x03\x04zip"
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="tone.zip"'

    upstream.answer = httpx.Response(403, json={"detail": "You may not share this skill."})
    response = await _call(app, "PUT", f"/api/skills/{uuid.uuid4()}/grants", json={"grants": []})
    assert response.status_code == 403
    assert response.json() == {"detail": "You may not share this skill."}
    assert json.loads(upstream.requests[-1].content) == {"grants": []}


@pytest.mark.anyio
async def test_an_upload_keeps_its_multipart_body(app: FastAPI, upstream: _Upstream) -> None:
    upstream.answer = httpx.Response(201, json={"id": "x"})
    response = await _call(
        app,
        "POST",
        "/api/skills/import",
        files={"file": ("SKILL.md", b"---\nname: a\ndescription: d\n---\nBody", "text/markdown")},
    )
    assert response.status_code == 201
    sent = upstream.requests[-1]
    assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b"name: a" in sent.content


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/skills/..%2Finternal%2Fskills%2Fresolve",
        "/api/skills/%2E%2E/internal/principals/user/x",
        "/api/skills/x%2F..%2F..%2Fhealth",
    ],
)
async def test_nothing_reaches_the_services_internal_api(
    path: str,
    app: FastAPI,
    upstream: _Upstream,
) -> None:
    """The internal API trusts the service token alone; the forwarder carries it."""
    response = await _call(app, "GET", path)
    assert response.status_code == 404
    assert upstream.requests == []


@pytest.mark.anyio
async def test_without_skills_read_there_is_no_skills_api() -> None:
    application = get_app()
    application.dependency_overrides[get_db_session] = _no_db
    application.dependency_overrides[current_active_user] = lambda: _user(superuser=False)
    dao = MagicMock(spec=RbacDAO)
    dao.has_permission = AsyncMock(return_value=False)
    application.dependency_overrides[RbacDAO] = lambda: dao
    response = await _call(application, "GET", "/api/skills")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_rbac_becomes_the_services_capabilities() -> None:
    assert await user_caps(_user(superuser=True), MagicMock()) == ("admin", "create", "share", "publish")

    def perm(resource: str, action: str) -> Any:
        return MagicMock(resource=resource, action=action)

    dao = MagicMock(spec=RbacDAO)
    dao.get_user_permissions = AsyncMock(
        return_value=[perm("skills", "read"), perm("skills", "share"), perm("chat.sessions", "create")],
    )
    assert await user_caps(_user(), dao) == ("share",)
    dao.get_user_permissions = AsyncMock(return_value=[perm("skills", "manage")])
    assert await user_caps(_user(), dao) == ("admin",)


def test_everyone_may_make_and_share_skills_and_admins_publish_them() -> None:
    assert _DEFAULT_ROLES["default_user"]["skills"] == ["read", "create", "share"]
    assert "publish" in _DEFAULT_ROLES["admin"]["skills"]
    assert "manage" in _DEFAULT_ROLES["admin"]["skills"]
    assert "publish" not in _DEFAULT_ROLES["operator"]["skills"]


# ── Drafting from a chat ──────────────────────────────────────────────


class _Gateway:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.sent: list[dict[str, Any]] = []

    async def list_messages(self, session_id: str, jwt: str, *, limit: int = 100) -> Any:
        return {
            "data": [
                {"role": "user", "content": "Review this diff for SQL injection."},
                {"role": "assistant", "content": "Line 3 builds SQL from input; use parameters."},
            ],
        }

    async def chat(self, payload: dict[str, Any], jwt: str) -> httpx.Response:
        self.sent.append(payload)
        return httpx.Response(200, json={"choices": [{"message": {"content": self.answer}}]})


@pytest.mark.anyio
async def test_a_draft_is_proposed_and_nothing_is_saved(
    app: FastAPI,
    upstream: _Upstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway(
        "<think>they want sql review</think>\n```markdown\n---\nname: SQL Review\n"
        "description: Checks diffs for SQL injection. Use when reviewing database code.\n"
        "---\n\n## Procedure\n1. Find string-built queries.\n```",
    )
    monkeypatch.setattr(skills_views, "shared_client", lambda: gateway)
    response = await _call(
        app,
        "POST",
        "/api/skills/draft",
        json={"model": "qwen", "session_id": "s-1", "guidance": "focus on SQL"},
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["slug"] == "sql-review"
    assert draft["name"] == "SQL Review"
    assert draft["body_markdown"] == "## Procedure\n1. Find string-built queries."
    assert draft["content"].startswith("---\nname: sql-review\n")
    [asked] = gateway.sent
    assert asked["model"] == "qwen"
    assert "session_id" not in asked  # the drafting call is not part of the chat
    assert "Review this diff" in asked["messages"][1]["content"]
    assert "focus on SQL" in asked["messages"][1]["content"]
    assert upstream.requests == []  # proposed, not saved


@pytest.mark.anyio
async def test_there_is_nothing_to_draft_from_an_empty_chat(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway("irrelevant")
    monkeypatch.setattr(skills_views, "shared_client", lambda: gateway)
    response = await _call(app, "POST", "/api/skills/draft", json={"model": "qwen", "messages": []})
    assert response.status_code == 422
    assert gateway.sent == []


def test_a_loose_answer_still_becomes_a_draft() -> None:
    plain = drafting.parse_draft("Just do the thing carefully.", fallback_name="From Chat")
    assert plain.slug == "from-chat"
    assert plain.description == ""
    assert plain.body_markdown == "Just do the thing carefully."

    bad_name = drafting.parse_draft("---\nname: '!!!'\ndescription: d\n---\nBody")
    assert bad_name.slug == "new-skill"

    unclosed_think = drafting.parse_draft("reasoning...</think>---\nname: ok\n---\nBody")
    assert unclosed_think.slug == "ok"
    assert unclosed_think.body_markdown == "Body"


def test_the_transcript_keeps_the_newest_turns() -> None:
    messages = [{"role": "user", "content": f"turn {i} " + "x" * 50} for i in range(100)]
    messages.append({"role": "system", "content": "hidden"})
    messages.append({"role": "tool", "content": "tool output"})
    text = drafting.transcript(messages, max_chars=300)
    assert "turn 99" in text
    assert "turn 0 " not in text
    assert "hidden" not in text
    assert "tool output" not in text
