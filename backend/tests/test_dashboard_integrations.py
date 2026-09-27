"""Dashboard integrations: a section per module that is on; a service that fails is named, not fatal."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.dashboard import attention as attention_rules
from faden_backend.services.dashboard import integrations
from faden_backend.services.dashboard.traffic import Attribution
from faden_backend.services.mcp.client import MCPServiceClient
from faden_backend.services.skills.client import SkillsPrincipal, SkillsServiceClient

pytestmark = pytest.mark.anyio()


class _Facts:
    """Enough of Facts for the integrations and the MCP attention rule."""

    def __init__(self) -> None:
        self.now = None
        local = SimpleNamespace(
            id=uuid.uuid4(), name="local", target="local_docker", source_kind=None, source_id=None
        )
        cloud = SimpleNamespace(
            id=uuid.uuid4(), name="OpenAI", target="remote_endpoint", source_kind=None, source_id=None
        )
        self._attribution = Attribution(
            providers=[local, cloud],
            runtimes=[],
            deployments={},
            environments={},
            residency={str(local.id): "machines", str(cloud.id): "external"},
        )
        self._integrations: integrations.Integrations | None = None

    async def attribution(self) -> Attribution:
        return self._attribution

    async def on_backend(self, _query: Any) -> int:
        return 1  # one remote provider not answering

    async def integrations(self) -> integrations.Integrations:
        if self._integrations is None:
            self._integrations = await integrations.load(self)  # type: ignore[arg-type]
        return self._integrations


async def test_modules_that_are_off_have_no_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations.settings, "mcp_enabled", False)
    monkeypatch.setattr(integrations.settings, "skills_enabled", False)

    found = await integrations.load(_Facts())  # type: ignore[arg-type]

    assert found.providers == {
        "total": 2,
        "by_residency": {"machines": 1, "private": 0, "external": 1, "unknown": 0},
        "unreachable": 1,
    }
    assert (found.mcp, found.skills, found.unavailable) == (None, None, [])


async def test_mcp_and_skills_are_counted_and_a_failing_service_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(integrations.settings, "mcp_enabled", True)
    monkeypatch.setattr(integrations.settings, "skills_enabled", True)

    async def servers(_self: object) -> dict[str, Any]:
        return {
            "servers": [
                {"id": "a", "name": "search", "enabled": True, "status": "active"},
                {
                    "id": "b",
                    "name": "files",
                    "enabled": True,
                    "status": "error",
                    "last_error": "connection refused",
                },
                {"id": "c", "name": "old", "enabled": False, "status": "disabled"},
            ]
        }

    async def skills(_self: object, *_a: object, **_kw: object) -> dict[str, Any]:
        raise RuntimeError("skills service down")

    monkeypatch.setattr(MCPServiceClient, "list_servers", servers)
    monkeypatch.setattr(SkillsServiceClient, "get_json", skills)
    facts = _Facts()

    found = await facts.integrations()

    assert found.mcp == {"total": 3, "enabled": 2, "healthy": 1, "problems": 1}
    assert found.skills is None
    assert found.unavailable == ["skills"]
    items = await attention_rules.mcp_servers(facts)  # type: ignore[arg-type]
    assert [(i.code, i.params["name"], i.params["message"]) for i in items] == [
        ("mcp_server_error", "files", "connection refused")
    ]


async def test_skills_are_counted_for_the_whole_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations.settings, "mcp_enabled", False)
    monkeypatch.setattr(integrations.settings, "skills_enabled", True)
    asked: list[tuple[str, SkillsPrincipal, dict[str, str]]] = []
    totals = {"": 7, "status=archived": 2, "usable=true": 3}

    async def get_json(
        _self: object, path: str, principal: SkillsPrincipal, params: list[tuple[str, str]]
    ) -> dict[str, Any]:
        query = dict(params)
        asked.append((path, principal, query))
        key = "&".join(f"{k}={v}" for k, v in params if k not in {"view", "limit"})
        return {"items": [], "total": totals[key], "offset": 0, "limit": 1}

    monkeypatch.setattr(SkillsServiceClient, "get_json", get_json)

    found = await integrations.load(_Facts())  # type: ignore[arg-type]

    # Archived skills are not counted; "active" is what a chat can be given.
    assert found.skills == {"total": 5, "active": 3}
    assert found.unavailable == []
    # Counts only (one row each), over everything a tenant admin sees.
    assert {path for path, _, _ in asked} == {"/api/skills"}
    assert all(q["view"] == "all" and q["limit"] == "1" for _, _, q in asked)
    assert all("admin" in p.caps for _, p, _ in asked)


async def test_non_superusers_are_refused(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession
) -> None:
    operator = User(
        id=uuid.uuid4(),
        email=f"int-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    dbsession.add(operator)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: operator

    resp = await client.get(fastapi_app.url_path_for("admin_dashboard_integrations"))

    assert resp.status_code == status.HTTP_403_FORBIDDEN
