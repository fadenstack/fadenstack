"""An agent turned off (sdk/51 8.3): its apps are refused with ``403 agent_disabled``; its profile says so.

Every route of the agent answers ``agent_disabled`` -- to its maintainers and administrators too -- but three: the
profile (``enabled: false``), the events and the manifest, so a device's record and inventory keep coming. The
device channel is refused the same way. Turning off or on again ends the agent's running grants (the backend asks,
with a grant only it can sign).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ApprovalGrant
from faden_gateway.services.gateway.agents import AgentProfile, UserFlags, decide
from faden_gateway.settings import settings
from faden_gateway.web.api.internal_agent_grants import AGENT_SWITCH_HEADER, AGENT_SWITCH_TOKEN_TYPE
from faden_gateway.web.api.v1.devices import authenticate_device
from tests.test_agent_routes import (  # noqa: F401 - fixtures
    AGENT_ID,
    MAINTAINER,
    USER,
    _agent_token,
    _Backend,
    _headers,
    _profile,
    _row,
    backend,
)
from tests.test_gateway_pipeline_combined import TEST_JWT_SECRET

OFF = {"code": "agent_disabled", "message": "Your organisation turned this agent off.", "type": "permission_error"}
#: What an agent that is off still takes.
STILL_ANSWERED = {
    ("GET", "/v1/agents/{agent}/profile"),
    ("POST", "/v1/agents/{agent}/events"),
    ("PUT", "/v1/agents/{agent}/manifest"),
}
ADMIN = "55555555-5555-5555-5555-555555555555"
OUTSIDER = "44444444-4444-4444-4444-444444444444"


def _off(fake: _Backend, fastapi_app: FastAPI, **overrides: Any) -> None:
    fake.put(_profile(enabled=False, **overrides))
    fastapi_app.state.agent_directory.invalidate()


def _agent_routes(fastapi_app: FastAPI) -> list[tuple[str, str]]:
    """Every ``/v1/agents/{agent}/...`` route the gateway has, by method (as its OpenAPI document lists them)."""
    paths = fastapi_app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/v1/agents/{agent}/")
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    )


def _url(path: str) -> str:
    return (
        path.replace("{agent}", "eplan-assistant").replace("{grant_id}", str(uuid.uuid4()))
        .replace("{approval_id}", "a1")
    )


async def _send(client: AsyncClient, method: str, path: str, token: str) -> Any:
    body: dict[str, Any] = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    if method in {"POST", "PUT", "PATCH"}:
        return await client.request(method, _url(path), headers=_headers(token), json=body)
    return await client.request(method, _url(path), headers=_headers(token))


def test_the_routes_are_all_known(fastapi_app: FastAPI) -> None:
    """A new route of an agent is refused while it is off, unless it is added here on purpose."""
    routes = _agent_routes(fastapi_app)
    assert len(routes) >= 20, routes
    assert set(routes) >= STILL_ANSWERED


@pytest.mark.anyio
@pytest.mark.parametrize("who", [USER, MAINTAINER, ADMIN], ids=["user", "maintainer", "administrator"])
async def test_every_route_of_an_agent_that_is_off_answers_agent_disabled_but_three(
    who: str, fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,  # noqa: F811
) -> None:
    backend.users[ADMIN] = UserFlags(is_active=True, is_superuser=True, chat_permissions=frozenset())
    _off(backend, fastapi_app)
    token = _agent_token(sub=who)
    for method, path in _agent_routes(fastapi_app):
        r = await _send(client, method, path, token)
        if (method, path) in STILL_ANSWERED:
            assert r.status_code < 400 or r.json()["error"]["code"] != "agent_disabled", (method, path, r.text)
            continue
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        error = r.json()["error"]
        assert {k: error.get(k) for k in OFF} == OFF, (method, path, r.text)


@pytest.mark.anyio
async def test_the_same_routes_are_not_refused_while_the_agent_is_on(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,  # noqa: F811
) -> None:
    for method, path in _agent_routes(fastapi_app):
        try:
            r = await _send(client, method, path, _agent_token())
        except Exception:  # noqa: BLE001, S112 - the route ran (past the switch) and needs what this test has not set up
            continue
        assert "agent_disabled" not in r.text, (method, path, r.text)


@pytest.mark.anyio
async def test_the_profile_says_whether_the_agent_is_on(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,  # noqa: F811
) -> None:
    on = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
    assert on.status_code == 200 and on.json()["enabled"] is True
    _off(backend, fastapi_app)
    off = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["slug"] == "eplan-assistant", "the rest of the profile as before"


@pytest.mark.anyio
async def test_an_agent_that_is_off_still_takes_events_and_the_manifest(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend, db_session: AsyncSession,  # noqa: F811
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: PLC0415

    fastapi_app.state.db_session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    _off(backend, fastapi_app)
    event = {"type": "tool.executed", "tool": "read_page", "duration_ms": 3, "outcome": "cancelled"}
    r = await client.post("/v1/agents/eplan-assistant/events", headers=_headers(), json={"events": [event]})
    assert r.status_code == 202, r.text
    r = await client.put("/v1/agents/eplan-assistant/manifest", headers=_headers(), json={"app": {"name": "EPLAN"}})
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_who_may_not_use_the_agent_hears_that_not_that_it_is_off(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,  # noqa: F811
) -> None:
    """The switch is told only to those the agent lets in; a retired agent stays retired."""
    _off(backend, fastapi_app)
    outsider = _agent_token(sub=OUTSIDER)
    backend.users[OUTSIDER] = UserFlags(is_active=True, is_superuser=False, chat_permissions=frozenset())
    r = await client.get("/v1/agents/eplan-assistant/models", headers=_headers(outsider))
    assert r.json()["error"]["code"] == "agent_not_granted"
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers(outsider))
    assert r.json()["error"]["code"] == "agent_not_granted", "nor its profile"
    _off(backend, fastapi_app, status="retired")
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
    assert r.json()["error"]["code"] == "agent_retired"


@pytest.mark.anyio
async def test_the_rule_itself() -> None:
    user = UserFlags(is_active=True, is_superuser=False, chat_permissions=frozenset())
    admin = UserFlags(is_active=True, is_superuser=True, chat_permissions=frozenset())
    off = _profile(enabled=False)
    for who, flags in ((USER, user), (MAINTAINER, user), (ADMIN, admin)):
        refused = await decide(off, user_id=who, user=flags, groups=())
        assert (refused.allowed, refused.status_code, refused.code) == (False, 403, "agent_disabled"), who
        assert (await decide(off, user_id=who, user=flags, groups=(), while_off=True)).allowed, who
        assert (await decide(_profile(), user_id=who, user=flags, groups=())).allowed, who
    inactive = UserFlags(is_active=False, is_superuser=False, chat_permissions=frozenset())
    assert (await decide(off, user_id=USER, user=inactive, groups=(), while_off=True)).code == "user_inactive"


def test_the_switch_is_read_from_the_backend_and_fails_closed() -> None:
    assert AgentProfile.from_row(_row(enabled=True)).enabled is True
    assert AgentProfile.from_row(_row(enabled=False)).enabled is False
    row = _row()
    del row["enabled"]
    assert AgentProfile.from_row(row).enabled is False, "a row without the column"
    assert AgentProfile.from_row(_row(enabled=None)).enabled is False


@pytest.mark.anyio
async def test_the_device_channel_refuses_an_agent_that_is_off(
    fastapi_app: FastAPI, backend: _Backend,  # noqa: F811
) -> None:
    assert (await authenticate_device(fastapi_app, _agent_token())).device_id == "device-7"
    _off(backend, fastapi_app)
    with pytest.raises(PermissionError) as refused:
        await authenticate_device(fastapi_app, _agent_token())
    assert refused.value.args == ("agent_disabled", "Your organisation turned this agent off.")


# -- the backend ends the agent's grants ------------------------------------------------------------


def _switch_grant(agent: str, *, typ: str = AGENT_SWITCH_TOKEN_TYPE, secret: str = TEST_JWT_SECRET) -> str:
    now = int(time.time())
    return jwt.encode({"typ": typ, "agent": agent, "iat": now, "exp": now + 60}, secret, algorithm="HS256")


def _grant(agent_id: str, *, status: str = "active", minutes: int = 30) -> ApprovalGrant:
    return ApprovalGrant(
        kind="time", tenant_id="tenant-a", user_id=USER, agent_id=agent_id, agent_version=3, device_id="device-7",
        scope={"tools": ["read_page"]}, expires_at=datetime.now(UTC) + timedelta(minutes=minutes), status=status,
    )


@pytest.mark.anyio
async def test_the_backend_ends_an_agents_running_grants_and_nobody_else_can(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "jwt_secret", TEST_JWT_SECRET)
    other = str(uuid.uuid4())
    grants = {
        "active": _grant(AGENT_ID), "paused": _grant(AGENT_ID, status="paused"),
        "expired": _grant(AGENT_ID, minutes=-5), "revoked": _grant(AGENT_ID, status="revoked"),
        "other agent": _grant(other),
    }
    db_session.add_all(grants.values())
    await db_session.flush()
    ids = {name: g.id for name, g in grants.items()}
    url, body = "/internal/agent-grants-end", {"agent": AGENT_ID}

    for refused in (
        {}, {AGENT_SWITCH_HEADER: "nonsense"}, {AGENT_SWITCH_HEADER: _switch_grant(other)},
        {AGENT_SWITCH_HEADER: _switch_grant(AGENT_ID, typ="chat_erase")},
        {AGENT_SWITCH_HEADER: _switch_grant(AGENT_ID, secret="another-secret-of-32-bytes-or-more")},  # noqa: S106
    ):
        r = await client.post(url, json=body, headers=refused)
        assert r.status_code == 403, refused

    r = await client.post(url, json=body, headers={AGENT_SWITCH_HEADER: _switch_grant(AGENT_ID)})
    assert r.status_code == 200, r.text
    assert r.json() == {"ended": 2}
    rows = {
        name: await db_session.get(ApprovalGrant, grant_id, populate_existing=True) for name, grant_id in ids.items()
    }
    assert {name: (g.status, g.revoked_by) for name, g in rows.items()} == {
        "active": ("revoked", "agent_turned_off"), "paused": ("revoked", "agent_turned_off"),
        "expired": ("active", None), "revoked": ("revoked", None), "other agent": ("active", None),
    }
    again = await client.post(url, json=body, headers={AGENT_SWITCH_HEADER: _switch_grant(AGENT_ID)})
    assert again.json() == {"ended": 0}, "idempotent"
    left = (await db_session.execute(select(ApprovalGrant).where(ApprovalGrant.status == "active"))).scalars().all()
    assert {g.agent_id for g in left} == {AGENT_ID, other}
