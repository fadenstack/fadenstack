"""Assigning skills to agents (sdk/51 F01b): what the backend checks before the skills service is asked.

The skills service does not know agents. The backend makes sure an assignment names only agents of this tenant
that the caller can see, that an agent's own list is for those who manage it, and that a deleted agent's
assignments go. The database is real; the skills service is a stand-in that records what it is sent.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.agents import Agent, AgentStatus
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.skills import client as skills_client
from faden_backend.services.skills.client import SkillsServiceClient, get_skills_client


class _As:
    """Who the API sees as signed in; switched within a test."""

    def __init__(self) -> None:
        self.user: User | None = None

    def __call__(self) -> User:
        assert self.user is not None
        return self.user


class _Skills:
    """The skills service: records each request; answers a skill's assignments from ``assigned``.

    A skill's set is refused as the service refuses it under its lock: an agent the skill does not have *then*
    (``assigned_at_save``, else ``assigned``) must be one the backend vetted.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.assigned: list[dict[str, Any]] = []
        self.assigned_at_save: list[dict[str, Any]] | None = None
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("skills service down", request=request)
        if request.method == "GET" and request.url.path.endswith("/assignments"):
            return httpx.Response(200, json=self.assigned)
        if request.method == "DELETE":
            return httpx.Response(200, json={"removed": 1})
        if request.method == "PUT" and request.url.path.endswith("/assignments/agents"):
            body = json.loads(request.content)
            rows = self.assigned if self.assigned_at_save is None else self.assigned_at_save
            has = {row["target_id"] for row in rows if row["target_type"] == "assistant"}
            vetted = body.get("vetted_agent_ids")
            unknown = [a for a in body["agent_ids"] if a not in has and vetted is not None and a not in vetted]
            if unknown:
                return httpx.Response(422, json={"detail": {
                    "code": "unknown_agent", "message": "No such agent: " + ", ".join(unknown), "ids": unknown,
                }})
        return httpx.Response(200, json=[])

    def sent(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]

    def client(self) -> SkillsServiceClient:
        return SkillsServiceClient("http://skills.local", "svc-token", transport=httpx.MockTransport(self))


@pytest.fixture
def skills(fastapi_app: FastAPI) -> _Skills:
    service = _Skills()
    fastapi_app.dependency_overrides[get_skills_client] = service.client
    return service


@pytest.fixture
def signed_in(fastapi_app: FastAPI) -> _As:
    who = _As()
    fastapi_app.dependency_overrides[current_active_user] = who
    return who


async def _user(db: AsyncSession, *, admin: bool = False, skills_read: bool = True) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="x",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    if skills_read and not admin:
        rbac = RbacDAO(db)
        await rbac.seed_defaults()
        known = {(p.resource, p.action): p.id for p in await rbac.list_permissions()}
        role = await rbac.create_role(
            f"skills-reader-{uuid.uuid4().hex[:6]}", None, [known[("skills", "read")]]
        )
        await rbac.assign_role(user.id, role.id)
        await db.flush()
    return user


async def _agent(client: AsyncClient, *, publish: bool = True) -> dict[str, Any]:
    slug = f"agent-{uuid.uuid4().hex[:8]}"
    r = await client.post(
        "/api/agents", json={"slug": slug, "name": slug, "version": {"instructions": "Brief."}}
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    if publish:
        r = await client.post(f"/api/agents/{slug}/versions/1/publish")
        assert r.status_code == status.HTTP_200_OK, r.text
    return r.json()


async def _put_agents(client: AsyncClient, skill_id: str, agent_ids: list[str]) -> httpx.Response:
    return await client.put(f"/api/skills/{skill_id}/assignments/agents", json={"agent_ids": agent_ids})


async def test_an_assignment_names_only_agents_of_this_tenant_the_caller_can_see(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    skills: _Skills,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    mine = await _agent(client)
    elsewhere = Agent(
        tenant_id="other",
        slug=f"other-{uuid.uuid4().hex[:6]}",
        name="Elsewhere",
        status=AgentStatus.PUBLISHED,
    )
    dbsession.add(elsewhere)
    await dbsession.flush()
    skill = str(uuid.uuid4())
    missing = str(uuid.uuid4())

    r = await _put_agents(client, skill, [mine["id"], missing, str(elsewhere.id), mine["slug"]])
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert r.json()["detail"]["code"] == "unknown_agent"
    assert r.json()["detail"]["ids"] == [missing, str(elsewhere.id), mine["slug"]], "a slug is not an id"
    assert skills.sent("PUT") == [], "nothing is forwarded"

    # An address may look like an id: it does not name the agent with that id.
    looks_like_an_id = str(uuid.uuid4())
    r = await client.post(
        "/api/agents", json={"slug": looks_like_an_id, "name": "Odd", "version": {"instructions": "x"}},
    )
    assert r.status_code == status.HTTP_201_CREATED, r.text
    r = await _put_agents(client, skill, [looks_like_an_id])
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, "an address is not an id"
    assert skills.sent("PUT") == []

    r = await _put_agents(client, skill, [mine["id"]])
    assert r.status_code == status.HTTP_200_OK, r.text
    [sent] = skills.sent("PUT")
    assert sent.url.path == f"/api/skills/{skill}/assignments/agents"
    assert json.loads(sent.content) == {"agent_ids": [mine["id"]], "vetted_agent_ids": [mine["id"]]}
    assert sent.headers["x-faden-user"] == str(signed_in.user.id), "forwarded as the caller"


async def test_an_agent_the_caller_cannot_see_is_unknown_unless_it_is_assigned_already(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    skills: _Skills,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    hidden = await _agent(client)
    shared = await _agent(client)
    reader = await _user(dbsession)
    r = await client.put(
        f"/api/agents/{shared['slug']}/grants",
        json={"grants": [{"principal_type": "user", "principal_id": str(reader.id)}]},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    skill = str(uuid.uuid4())

    signed_in.user = reader
    r = await _put_agents(client, skill, [hidden["id"]])
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert r.json()["detail"]["ids"] == [hidden["id"]], "not told apart from a missing agent"
    assert (await _put_agents(client, skill, [shared["id"]])).status_code == status.HTTP_200_OK, (
        "an agent they may use is one they can see"
    )

    skills.assigned = [
        {"target_type": "assistant", "target_id": hidden["id"]},
        {"target_type": "assistant", "target_id": "typed-in-the-old-dialog"},
    ]
    r = await _put_agents(client, skill, [hidden["id"], "typed-in-the-old-dialog", shared["id"]])
    assert r.status_code == status.HTTP_200_OK, "what the skill has already may stay in the set"
    assert json.loads(skills.sent("PUT")[-1].content)["vetted_agent_ids"] == [shared["id"]], (
        "only the agent the caller can see is vouched for"
    )
    assert admin is not None


async def test_an_agent_removed_after_the_backend_read_counts_as_new(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    skills: _Skills,
) -> None:
    """The backend reads the skill's agents, then the skills service saves: between the two another change may
    remove an agent. The service decides under its lock from the agents the backend vouched for, so the stale
    "assigned already" lets nobody add an agent they cannot see (ASVS 5.0 V8)."""
    signed_in.user = await _user(dbsession, admin=True)
    hidden = await _agent(client)
    signed_in.user = await _user(dbsession)
    skill = str(uuid.uuid4())
    skills.assigned = [{"target_type": "assistant", "target_id": hidden["id"]}]  # what the backend reads
    skills.assigned_at_save = []  # removed by another change before the save

    r = await _put_agents(client, skill, [hidden["id"]])

    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert r.json()["detail"] == {"code": "unknown_agent", "message": f"No such agent: {hidden['id']}", "ids": [hidden["id"]]}


@pytest.mark.parametrize(
    "agent_ids",
    [
        [str(uuid.uuid4()) for _ in range(101)],
        ["same", "same"],
        [""],
        ["x" * 129],
    ],
    ids=["101 ids", "twice", "empty", "too long"],
)
async def test_the_lists_are_bounded(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    skills: _Skills,
    agent_ids: list[str],
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    r = await _put_agents(client, str(uuid.uuid4()), agent_ids)
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    agent = await _agent(client)
    r = await client.put(
        f"/api/skills/assignments/agents/{agent['id']}",
        json={"skill_ids": [str(uuid.uuid4()) for _ in range(101)]},
    )
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    same = str(uuid.uuid4())
    r = await client.put(f"/api/skills/assignments/agents/{agent['id']}", json={"skill_ids": [same, same]})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert skills.requests == [], "nothing reaches the skills service"


async def test_an_agents_own_skills_are_for_those_who_manage_it(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    skills: _Skills,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    agent = await _agent(client)
    maintainer, user = await _user(dbsession), await _user(dbsession)
    r = await client.put(
        f"/api/agents/{agent['slug']}/members", json={"members": [{"user_id": str(maintainer.id)}]}
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    r = await client.put(
        f"/api/agents/{agent['slug']}/grants",
        json={"grants": [{"principal_type": "user", "principal_id": str(user.id)}]},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    path = f"/api/skills/assignments/agents/{agent['id']}"
    skill = str(uuid.uuid4())

    signed_in.user = user
    assert (await client.get(path)).status_code == status.HTTP_403_FORBIDDEN, (
        "using an agent is not managing it"
    )
    assert (await client.put(path, json={"skill_ids": [skill]})).status_code == status.HTTP_403_FORBIDDEN
    assert skills.requests == []

    signed_in.user = maintainer
    assert (await client.get(path)).status_code == status.HTTP_200_OK
    r = await client.put(path, json={"skill_ids": [skill]})
    assert r.status_code == status.HTTP_200_OK
    assert [(s.method, s.url.path) for s in skills.requests] == [("GET", path), ("PUT", path)]
    assert json.loads(skills.requests[-1].content) == {"skill_ids": [skill]}

    nobody = f"/api/skills/assignments/agents/{uuid.uuid4()}"
    assert (await client.get(nobody)).status_code == status.HTTP_404_NOT_FOUND
    assert len(skills.requests) == 2


async def test_deleting_an_agent_lets_go_of_its_assignments_and_never_waits_on_them(
    signed_in: _As,
    client: AsyncClient,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _Skills()
    monkeypatch.setattr(skills_client, "skills_client_if_enabled", service.client)
    signed_in.user = await _user(dbsession, admin=True)

    gone = await _agent(client)
    await client.post(f"/api/agents/{gone['slug']}/retire")
    assert (await client.delete(f"/api/agents/{gone['slug']}")).status_code == status.HTTP_204_NO_CONTENT
    [sent] = service.sent("DELETE")
    assert sent.url.path == f"/api/internal/agents/{gone['id']}/assignments"
    assert "x-faden-user" not in sent.headers, "the service's own call, as nobody"

    service.fail = True
    also = await _agent(client)
    await client.post(f"/api/agents/{also['slug']}/retire")
    with caplog.at_level(logging.WARNING):
        r = await client.delete(f"/api/agents/{also['slug']}")
    assert r.status_code == status.HTTP_204_NO_CONTENT, (
        "a skills service that is down does not block the delete"
    )
    assert (await client.get(f"/api/agents/{also['slug']}")).status_code == status.HTTP_404_NOT_FOUND
    assert any(also["id"] in record.getMessage() for record in caplog.records)
