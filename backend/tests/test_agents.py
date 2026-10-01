"""Agents: who may use one, its versions, and the tokens an SDK signs in with.

Default deny: grants to users and teams allow; maintainers may use drafts;
retired agents and agents with settings no installed extension enforces are
closed to everyone else. Agent tokens are bound to one agent, refresh tokens
rotate, and a reused refresh token ends the sign-in everywhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.agents import AgentRefreshToken, AgentVersion
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.extensions import registry
from faden_backend.settings import settings


class _As:
    """Who the API sees as signed in; switched within a test."""

    def __init__(self) -> None:
        self.user: User | None = None

    def __call__(self) -> User:
        assert self.user is not None
        return self.user


async def _user(db: AsyncSession, *, admin: bool = False, active: bool = True) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="x",  # noqa: S106
        is_active=active,
        is_verified=True,
        is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


async def _team(db: AsyncSession, *members: User) -> Group:
    group = Group(name=f"team-{uuid.uuid4().hex[:8]}")
    db.add(group)
    await db.flush()
    db.add_all([UserGroup(user_id=m.id, group_id=group.id) for m in members])
    await db.flush()
    return group


@pytest.fixture
def signed_in(fastapi_app: FastAPI) -> _As:
    who = _As()
    fastapi_app.dependency_overrides[current_active_user] = who
    return who


@pytest.fixture
def secret() -> str:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-agent-secret-0123456789abcdef")
    return settings.users_secret


@pytest.fixture
def clean_registry() -> Iterator[None]:
    saved = (list(registry.access_rules), dict(registry.version_extensions), registry.token_lifetimes)
    yield
    registry.access_rules[:] = saved[0]
    registry.version_extensions.clear()
    registry.version_extensions.update(saved[1])
    registry.token_lifetimes = saved[2]


async def _agent(
    client: AsyncClient, *, slug: str | None = None, publish: bool = True, **version: Any,
) -> dict[str, Any]:
    slug = slug or f"agent-{uuid.uuid4().hex[:8]}"
    body = {"slug": slug, "name": slug.title(), "version": {"instructions": "Be brief.", **version}}
    r = await client.post("/api/agents", json=body)
    assert r.status_code == status.HTTP_201_CREATED, r.text
    if publish:
        r = await client.post(f"/api/agents/{slug}/versions/1/publish")
        assert r.status_code == status.HTTP_200_OK, r.text
    return r.json()


async def _grant(client: AsyncClient, slug: str, *grants: tuple[str, uuid.UUID]) -> None:
    r = await client.put(
        f"/api/agents/{slug}/grants",
        json={"grants": [{"principal_type": t, "principal_id": str(i)} for t, i in grants]},
    )
    assert r.status_code == status.HTTP_200_OK, r.text


async def _access(client: AsyncClient, slug: str, user: User) -> dict[str, Any]:
    r = await client.get(f"/api/agents/{slug}/access/{user.id}")
    assert r.status_code == status.HTTP_200_OK, r.text
    return r.json()


# -- agents and versions ----------------------------------------------------------


async def test_an_agent_starts_as_a_draft_its_creator_maintains(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    agent = await _agent(client, slug="eplan-assistant", publish=False, model_alias="qwen3-32b")
    assert agent["status"] == "draft"
    assert agent["latest_version"] == 1 and agent["published_version"] is None
    members = (await client.get("/api/agents/eplan-assistant/members")).json()
    assert members == [{"user_id": str(admin.id), "role": "maintainer", "label": admin.email}]
    # Addressed by slug or id alike.
    assert (await client.get(f"/api/agents/{agent['id']}")).json()["slug"] == "eplan-assistant"


async def test_a_slug_is_unique_and_well_formed(signed_in: _As, client: AsyncClient, dbsession: AsyncSession) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="tia-assistant")
    r = await client.post("/api/agents", json={"slug": "tia-assistant", "name": "Again"})
    assert r.status_code == status.HTTP_409_CONFLICT
    r = await client.post("/api/agents", json={"slug": "Not A Slug", "name": "x"})
    assert r.status_code == 422


async def test_creating_needs_the_permission(signed_in: _As, client: AsyncClient, dbsession: AsyncSession) -> None:
    signed_in.user = await _user(dbsession)
    r = await client.post("/api/agents", json={"slug": "mine", "name": "Mine"})
    assert r.status_code == status.HTTP_403_FORBIDDEN


async def test_versions_are_added_published_and_rolled_back(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="office-helper")
    r = await client.post("/api/agents/office-helper/versions", json={"instructions": "Be thorough."})
    assert r.status_code == status.HTTP_201_CREATED and r.json()["version"] == 2
    assert (await client.get("/api/agents/office-helper")).json()["published_version"] == 1

    await client.post("/api/agents/office-helper/versions/2/publish")
    assert (await client.get("/api/agents/office-helper")).json()["published_version"] == 2
    await client.post("/api/agents/office-helper/versions/1/publish")
    assert (await client.get("/api/agents/office-helper")).json()["published_version"] == 1
    listed = (await client.get("/api/agents/office-helper/versions")).json()
    assert [v["version"] for v in listed] == [2, 1]
    assert listed[1]["instructions"] == "Be brief."


async def test_host_tool_names_are_unique(signed_in: _As, client: AsyncClient, dbsession: AsyncSession) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    r = await client.post("/api/agents", json={
        "slug": "dup-tools", "name": "x",
        "version": {"host_tool_contract": [{"name": "read_page"}, {"name": "read_page"}]},
    })
    assert r.status_code == status.HTTP_400_BAD_REQUEST


async def test_enterprise_settings_are_refused_without_an_extension_to_enforce_them(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, clean_registry: None,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    r = await client.post("/api/agents", json={
        "slug": "locked", "name": "x", "version": {"extensions": {"local_mcp_filter": {"lock": True}}},
    })
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert "Enterprise" in r.json()["detail"]

    def check(value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("expected an object")

    registry.version_extensions["local_mcp_filter"] = check
    r = await client.post("/api/agents", json={
        "slug": "locked", "name": "x", "version": {"extensions": {"local_mcp_filter": {"lock": True}}},
    })
    assert r.status_code == status.HTTP_201_CREATED, r.text
    r = await client.post("/api/agents/locked/versions", json={"extensions": {"local_mcp_filter": 3}})
    assert r.status_code == status.HTTP_400_BAD_REQUEST


# -- who may use it ---------------------------------------------------------------------


async def test_access_follows_grants_to_users_and_teams(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    direct, in_team, outsider = await _user(dbsession), await _user(dbsession), await _user(dbsession)
    team = await _team(dbsession, in_team)
    await _agent(client, slug="plc-helper")

    assert (await _access(client, "plc-helper", direct))["code"] == "no_grant", "default deny"
    await _grant(client, "plc-helper", ("user", direct.id), ("group", team.id))

    assert (await _access(client, "plc-helper", direct))["allowed"] is True
    team_access = await _access(client, "plc-helper", in_team)
    assert team_access["allowed"] is True and team.name in team_access["detail"]
    assert (await _access(client, "plc-helper", outsider))["allowed"] is False


async def test_drafts_are_for_maintainers_and_retired_agents_for_nobody(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="drafty", publish=False)
    await _grant(client, "drafty", ("user", user.id))
    assert (await _access(client, "drafty", user))["code"] == "not_published"
    assert (await _access(client, "drafty", admin))["code"] == "maintainer"

    await client.post("/api/agents/drafty/versions/1/publish")
    assert (await _access(client, "drafty", user))["allowed"] is True
    await client.delete("/api/agents/drafty")
    assert (await _access(client, "drafty", user))["code"] == "agent_retired"
    await client.post("/api/agents/drafty/restore")
    assert (await _access(client, "drafty", user))["allowed"] is True


async def test_a_deactivated_user_and_an_access_rule_deny(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, clean_registry: None,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    gone, excluded = await _user(dbsession, active=False), await _user(dbsession)
    team = await _team(dbsession, gone, excluded)
    await _agent(client, slug="ruled")
    await _grant(client, "ruled", ("group", team.id))
    assert (await _access(client, "ruled", gone))["code"] == "user_inactive"

    async def exclude(user_id: uuid.UUID, agent_id: uuid.UUID) -> str | None:
        return "Excluded from this agent." if user_id == excluded.id else None

    registry.access_rules.append(exclude)
    decision = await _access(client, "ruled", excluded)
    assert decision == {"allowed": False, "code": "denied_by_rule", "detail": "Excluded from this agent."}


async def test_settings_nothing_enforces_close_the_agent_to_all_but_maintainers(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    agent = await _agent(client, slug="was-enterprise")
    await _grant(client, "was-enterprise", ("user", user.id))
    # Written while Enterprise was installed; it is gone now.
    version = (await dbsession.execute(
        select(AgentVersion).where(AgentVersion.agent_id == uuid.UUID(agent["id"])),
    )).scalar_one()
    version.extensions = {"pii": {"mode": "device_only"}}
    await dbsession.flush()

    assert (await _access(client, "was-enterprise", user))["code"] == "needs_enterprise"
    assert (await _access(client, "was-enterprise", admin))["allowed"] is True


async def test_a_user_lists_only_the_agents_they_can_use(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="granted")
    await _agent(client, slug="not-granted")
    await _agent(client, slug="granted-draft", publish=False)
    await _grant(client, "granted", ("user", user.id))
    await _grant(client, "granted-draft", ("user", user.id))

    signed_in.user = user
    assert [a["slug"] for a in (await client.get("/api/agents")).json()] == ["granted"]
    assert (await client.get("/api/agents/not-granted")).status_code == status.HTTP_404_NOT_FOUND
    assert (await client.put("/api/agents/granted/grants", json={"grants": []})).status_code == 403


async def test_agents_read_sees_an_agents_details_but_changes_nothing(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, fastapi_app: FastAPI,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="inspected")

    signed_in.user = await _user(dbsession)
    reader = MagicMock(spec=RbacDAO)
    reader.has_permission = AsyncMock(
        side_effect=lambda _user, resource, action: (resource, action) == ("agents", "read"),
    )
    fastapi_app.dependency_overrides[RbacDAO] = lambda: reader
    try:
        agent = (await client.get("/api/agents/inspected")).json()
        assert agent["can_manage"] is False
        for path in ("versions", "versions/1", "grants", "members", "sign-ins"):
            assert (await client.get(f"/api/agents/inspected/{path}")).status_code == status.HTTP_200_OK, path
        for path in ("usage", "events"):  # served from the trace database, which tests have none of
            assert (await client.get(f"/api/agents/inspected/{path}")).status_code != 403, path
        assert (await client.put("/api/agents/inspected/grants", json={"grants": []})).status_code == 403
        assert (await client.post("/api/agents/inspected/versions", json={})).status_code == 403
        assert (await client.post("/api/agents/inspected/versions/1/publish")).status_code == 403
    finally:
        fastapi_app.dependency_overrides.pop(RbacDAO)


async def test_grants_name_known_principals_only(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="strict")
    r = await client.put("/api/agents/strict/grants", json={
        "grants": [{"principal_type": "group", "principal_id": str(uuid.uuid4())}],
    })
    assert r.status_code == status.HTTP_400_BAD_REQUEST


async def test_grants_and_members_are_labelled_and_principals_found_by_agent_managers(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    member = await _user(dbsession)
    team = await _team(dbsession, member)
    await _agent(client, slug="labelled")
    await _grant(client, "labelled", ("group", team.id), ("user", member.id))
    grants = (await client.get("/api/agents/labelled/grants")).json()
    assert {(g["principal_type"], g["label"]) for g in grants} == {("group", team.name), ("user", member.email)}
    r = await client.put("/api/agents/labelled/members", json={"members": [
        {"user_id": str(admin.id)}, {"user_id": str(member.id)},
    ]})
    assert sorted(m["label"] for m in r.json()) == sorted([admin.email, member.email])

    found = (await client.get("/api/agents/principals", params={"q": member.email[:6]})).json()
    assert {"type": "user", "id": str(member.id), "label": member.email} in found
    named = (await client.get("/api/agents/principals", params={"ids": f"group:{team.id}"})).json()
    assert named == [{"type": "group", "id": str(team.id), "label": team.name}]

    # A maintainer without RBAC rights may look principals up; someone who manages no agent may not.
    signed_in.user = member
    assert (await client.get("/api/agents/principals", params={"q": "exam"})).status_code == status.HTTP_200_OK
    signed_in.user = await _user(dbsession)
    r = await client.get("/api/agents/principals", params={"q": "exam"})
    assert r.status_code == status.HTTP_403_FORBIDDEN


# -- tokens --------------------------------------------------------------------------------


async def _sign_in(client: AsyncClient, slug: str, device: str = "EPLAN on PC-7") -> Any:
    return await client.post("/api/agent-tokens", json={"agent": slug, "device_label": device})


async def test_a_token_is_bound_to_its_agent_and_marked_as_an_agent_token(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    agent = await _agent(client, slug="bound")
    await _grant(client, "bound", ("user", user.id))

    signed_in.user = user
    r = await _sign_in(client, "bound")
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    claims = jwt.decode(body["access_token"], secret, algorithms=["HS256"], audience=f"agent:{agent['id']}")
    assert claims["sub"] == str(user.id)
    assert claims["typ"] == "agent" and claims["agent"] == "bound"
    assert claims["did"] == body["device_id"]
    assert claims["dev"] is False
    assert claims["exp"] - claims["iat"] == body["expires_in"] == settings.agent_access_token_ttl_s
    # Only the hash of the refresh token is kept.
    stored = (await dbsession.execute(select(AgentRefreshToken.token_hash))).scalars().all()
    assert body["refresh_token"] not in stored


async def test_no_token_without_access(signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="closed")
    signed_in.user = await _user(dbsession)
    r = await _sign_in(client, "closed")
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert r.json()["detail"]["code"] == "no_grant"


async def test_refresh_rotates_and_a_reused_token_ends_the_sign_in(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="rotating")
    await _grant(client, "rotating", ("user", user.id))
    signed_in.user = user
    first = (await _sign_in(client, "rotating")).json()

    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == status.HTTP_200_OK, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["device_id"] == first["device_id"]

    # The old one again: someone has a copy. Both stop working.
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    assert r.json()["detail"]["code"] == "refresh_token_reused"
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


async def test_refresh_is_refused_once_the_grant_is_gone(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    team = await _team(dbsession, user)
    await _agent(client, slug="team-only")
    await _grant(client, "team-only", ("group", team.id))
    signed_in.user = user
    tokens = (await _sign_in(client, "team-only")).json()

    signed_in.user = admin
    await _grant(client, "team-only")
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert r.json()["detail"]["code"] == "no_grant"
    # And the sign-in is over, even if the grant comes back.
    await _grant(client, "team-only", ("group", team.id))
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


async def test_an_idle_or_expired_sign_in_ends(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="expiring")
    await _grant(client, "expiring", ("user", user.id))
    signed_in.user = user
    idle = (await _sign_in(client, "expiring", "idle")).json()
    old = (await _sign_in(client, "expiring", "old")).json()
    past = datetime.now(UTC) - timedelta(seconds=1)
    rows = {r.family_id: r for r in (await dbsession.execute(select(AgentRefreshToken))).scalars()}
    rows[uuid.UUID(idle["device_id"])].idle_expires_at = past
    rows[uuid.UUID(old["device_id"])].expires_at = past
    await dbsession.flush()

    for tokens in (idle, old):
        r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == status.HTTP_401_UNAUTHORIZED
        assert r.json()["detail"]["code"] == "refresh_token_expired"


async def test_a_lowered_lifetime_takes_effect_at_the_next_refresh(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="shortened")
    await _grant(client, "shortened", ("user", user.id))
    signed_in.user = user
    tokens = (await _sign_in(client, "shortened")).json()
    assert tokens["refresh_expires_in"] == settings.agent_refresh_token_ttl_s

    monkeypatch.setattr(settings, "agent_refresh_token_ttl_s", 3600)
    monkeypatch.setattr(settings, "agent_access_token_ttl_s", 60)
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
    body = r.json()
    assert body["expires_in"] == 60
    assert 3500 < body["refresh_expires_in"] <= 3600


async def test_sign_ins_are_listed_and_revoked_by_their_user_or_a_maintainer(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user, other = await _user(dbsession), await _user(dbsession)
    await _agent(client, slug="devices")
    await _grant(client, "devices", ("user", user.id), ("user", other.id))
    signed_in.user = user
    tokens = (await _sign_in(client, "devices", "Laptop")).json()
    mine = (await client.get("/api/agent-tokens/mine")).json()
    assert [(s["device_label"], s["device_id"]) for s in mine] == [("Laptop", tokens["device_id"])]

    signed_in.user = other
    r = await client.delete(f"/api/agent-tokens/{tokens['device_id']}")
    assert r.status_code == status.HTTP_404_NOT_FOUND, "not someone else's"

    signed_in.user = admin
    assert len((await client.get("/api/agents/devices/sign-ins")).json()) == 1
    r = await client.delete(f"/api/agent-tokens/{tokens['device_id']}")
    assert r.status_code == status.HTTP_204_NO_CONTENT
    r = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


async def test_token_lifetimes_come_from_the_registered_policy(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str, clean_registry: None,
) -> None:
    from faden_backend.services.extensions import Lifetimes

    class PerAgent:
        def lifetimes(self, extensions: dict[str, Any]) -> Lifetimes:
            return Lifetimes(access_s=extensions.get("token", {}).get("access_s", 5), refresh_s=100, idle_s=50)

    registry.token_lifetimes = PerAgent()
    registry.version_extensions["token"] = lambda value: None
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="per-agent", extensions={"token": {"access_s": 120}})
    await _grant(client, "per-agent", ("user", user.id))
    signed_in.user = user
    body = (await _sign_in(client, "per-agent")).json()
    assert (body["expires_in"], body["refresh_expires_in"]) == (120, 100)


# -- developer mode -----------------------------------------------------------------------


async def test_developer_mode_is_set_by_admins_and_reaches_the_token(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    dev = await _user(dbsession)
    await _agent(client, slug="dev-agent")
    await _grant(client, "dev-agent", ("user", dev.id))

    signed_in.user = dev
    r = await client.put(f"/api/admin/users/{dev.id}/developer-mode", json={"enabled": True})
    assert r.status_code == status.HTTP_403_FORBIDDEN, "never self-service"

    signed_in.user = admin
    r = await client.put(f"/api/admin/users/{dev.id}/developer-mode", json={"enabled": True})
    assert r.status_code == status.HTTP_200_OK and r.json()["developer_mode"] is True
    listed = {u["id"]: u for u in (await client.get("/api/admin/users/")).json()}
    assert listed[str(dev.id)]["developer_mode"] is True, "the users page shows it"
    signed_in.user = dev
    token = (await _sign_in(client, "dev-agent")).json()["access_token"]
    assert jwt.decode(token, options={"verify_signature": False})["dev"] is True

    # Switched on until a moment ago: off.
    signed_in.user = admin
    until = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await client.put(f"/api/admin/users/{dev.id}/developer-mode", json={"enabled": True, "until": until})
    signed_in.user = dev
    token = (await _sign_in(client, "dev-agent")).json()["access_token"]
    assert jwt.decode(token, options={"verify_signature": False})["dev"] is False
