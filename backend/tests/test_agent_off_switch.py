"""Section 8's server parts in the backend (sdk/51): the agent's off-switch, the device block shown per device, and
access tokens of five minutes or less.

- **The switch** (8.3): those who manage an agent turn it off and on (``POST .../turn-off``, ``.../turn-on``),
  audited. Its running grants end in the gateway; turning it on again needs them ended, or it stays off. Sign-ins
  stay; the access check says the agent is off.
- **The device block** (8.3): each sign-in carries what its device last reported, from the gateway's table.
- **Access tokens** (8.11): 300 s at most, whatever the setting, a stored older value or an extension says.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.system_settings import SystemSettingValue
from faden_backend.services.chat import gateway_client
from faden_backend.services.extensions import Lifetimes, registry
from faden_backend.services.system_settings.registry import OutOfRangeError, registry_by_key, validate_value
from faden_backend.settings import settings
from tests.test_agents import (  # noqa: F401 - fixtures
    MIGRATIONS,
    _access,
    _agent,
    _As,
    _grant,
    _migration,
    _sign_in,
    _user,
    clean_registry,
    secret,
    signed_in,
)

OFF_SWITCH_MIGRATION = MIGRATIONS / "2026-11-16-18-00_0ffsw1t.py"


class _Gateway:
    """The gateway's grants, as the backend reaches them; *fail* as if it could not be reached."""

    def __init__(self) -> None:
        self.fail = False
        self.ended: list[str] = []

    async def end_agent_grants(self, agent_id: str, *, secret: str) -> int:
        assert secret, "the call is signed"
        if self.fail:
            raise gateway_client.GrantsEndFailed("The gateway could not be reached.")
        self.ended.append(agent_id)
        return 2


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, secret: str) -> _Gateway:  # noqa: F811
    fake = _Gateway()
    monkeypatch.setattr(gateway_client, "shared_client", lambda: fake)
    return fake


async def _audits(db: AsyncSession, action: str) -> list[dict[str, Any]]:
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.action == action))).scalars().all()
    return [json.loads(r.metadata_json or "{}") for r in rows]


# -- the switch --------------------------------------------------------------------------------------


async def test_an_agent_is_turned_off_and_on_again(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, gateway: _Gateway,  # noqa: F811
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    agent = await _agent(client, slug="switched")
    assert agent["enabled"] is True and agent["turned_off_at"] is None

    off = await client.post("/api/agents/switched/turn-off")
    assert off.status_code == status.HTTP_200_OK, off.text
    body = off.json()
    assert body["enabled"] is False and body["turned_off_by"] == str(admin.id)
    assert body["turned_off_at"] is not None
    assert gateway.ended == [agent["id"]], "its running grants end"
    assert await _audits(dbsession, "agent.turn_off") == [{"grants_ended": 2}]

    again = (await client.post("/api/agents/switched/turn-off")).json()
    assert again["turned_off_at"] == body["turned_off_at"], "off again changes nothing"
    assert len(gateway.ended) == 1 and len(await _audits(dbsession, "agent.turn_off")) == 1

    on = await client.post("/api/agents/switched/turn-on")
    assert on.status_code == status.HTTP_200_OK, on.text
    assert (on.json()["enabled"], on.json()["turned_off_at"], on.json()["turned_off_by"]) == (True, None, None)
    assert gateway.ended == [agent["id"]] * 2, "no grant from before counts again"
    assert await _audits(dbsession, "agent.turn_on") == [{"grants_ended": 2}]
    assert (await client.post("/api/agents/switched/turn-on")).json()["enabled"] is True
    assert len(gateway.ended) == 2, "on again changes nothing"


async def test_an_agent_stays_off_while_the_gateway_cannot_end_its_grants(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, gateway: _Gateway,  # noqa: F811
) -> None:
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="unreached")
    gateway.fail = True
    off = await client.post("/api/agents/unreached/turn-off")
    assert off.status_code == status.HTTP_200_OK and off.json()["enabled"] is False, "the switch holds anyway"
    assert await _audits(dbsession, "agent.turn_off") == [{"grants_ended": None}]

    on = await client.post("/api/agents/unreached/turn-on")
    assert on.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, on.text
    assert "stays off" in on.json()["detail"]
    assert (await client.get("/api/agents/unreached")).json()["enabled"] is False
    gateway.fail = False
    assert (await client.post("/api/agents/unreached/turn-on")).json()["enabled"] is True


async def test_only_those_who_manage_the_agent_switch_it(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, gateway: _Gateway,  # noqa: F811
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="not-yours")
    await _grant(client, "not-yours", ("user", user.id))
    assert (await client.post("/api/agents/not-yours/turn-off")).status_code == status.HTTP_200_OK

    signed_in.user = user
    for action in ("turn-on", "turn-off"):
        r = await client.post(f"/api/agents/not-yours/{action}")
        assert r.status_code == status.HTTP_403_FORBIDDEN, (action, r.text)
    seen = (await client.get("/api/agents/not-yours")).json()
    assert seen["enabled"] is False and seen["turned_off_at"] is not None
    assert seen["turned_off_by"] is None, "who turned it off is told to those who manage it"
    signed_in.user = admin
    assert (await client.get("/api/agents/not-yours")).json()["turned_off_by"] == str(admin.id)


async def test_while_off_the_access_check_says_so_and_sign_ins_stay(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, gateway: _Gateway,  # noqa: F811
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    await _agent(client, slug="paused-agent")
    await _grant(client, "paused-agent", ("user", user.id))
    signed_in.user = user
    tokens = (await _sign_in(client, "paused-agent")).json()

    signed_in.user = admin
    await client.post("/api/agents/paused-agent/turn-off")
    checked = await _access(client, "paused-agent", user)
    assert (checked["allowed"], checked["code"]) == (False, "agent_disabled")
    assert checked["detail"].startswith("The agent is turned off: nobody can use it until it is turned on again.")
    assert checked["detail"].endswith("Otherwise: Allowed through a grant to the user."), "and why it would be allowed"

    signed_in.user = user
    assert (await _sign_in(client, "paused-agent", "another PC")).status_code == status.HTTP_201_CREATED
    refreshed = await client.post("/api/agent-tokens/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == status.HTTP_200_OK, "the gateway refuses the use, not the sign-in"

    signed_in.user = admin
    await client.post("/api/agents/paused-agent/turn-on")
    assert (await _access(client, "paused-agent", user))["allowed"] is True


# -- access tokens of five minutes or less ----------------------------------------------------------------


@pytest.mark.parametrize(("stored", "lives"), [(600, 300), (86_400, 300), (300, 300), (120, 120), (60, 60)])
async def test_an_access_token_lives_five_minutes_at_most(
    stored: int, lives: int, signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
    secret: str, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.setattr(settings, "agent_access_token_ttl_s", stored)
    signed_in.user = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    agent = await _agent(client, slug=f"short-{stored}")
    await _grant(client, f"short-{stored}", ("user", user.id))
    signed_in.user = user
    body = (await _sign_in(client, f"short-{stored}")).json()
    claims = jwt.decode(body["access_token"], secret, algorithms=["HS256"], audience=f"agent:{agent['id']}")
    assert claims["exp"] - claims["iat"] == body["expires_in"] == lives
    refreshed = (await client.post("/api/agent-tokens/refresh", json={"refresh_token": body["refresh_token"]})).json()
    assert refreshed["expires_in"] == lives


class _LongLived:
    """An extension's lifetimes, longer than the ceiling."""

    def lifetimes(self, extensions: dict[str, Any]) -> Lifetimes:
        del extensions
        return Lifetimes(access_s=900, refresh_s=3600, idle_s=600)


async def test_an_extensions_longer_lifetime_is_cut_too(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str, clean_registry: None,  # noqa: F811
) -> None:
    registry.token_lifetimes = _LongLived()
    signed_in.user = await _user(dbsession, admin=True)
    await _agent(client, slug="extended")
    body = (await _sign_in(client, "extended")).json()
    assert body["expires_in"] == 300
    assert body["refresh_expires_in"] == 3600, "only the access token has the ceiling"


def test_the_setting_takes_sixty_to_three_hundred_seconds() -> None:
    definition = registry_by_key()["agents.access_token_ttl_s"]
    assert (definition.default, definition.min_value, definition.max_value) == (300, 60, 300)
    assert settings.model_fields["agent_access_token_ttl_s"].default == 300
    for fine in (60, 300):
        assert validate_value(definition, fine) == fine
    for wrong in (59, 301, 600):
        with pytest.raises(OutOfRangeError):
            validate_value(definition, wrong)


# -- the migration ---------------------------------------------------------------------------------------


async def test_the_off_switch_migration_upgrades_and_downgrades(dbsession: AsyncSession) -> None:
    """Down and up on the test database (made from the models), inside the test's transaction.

    A stored lifetime over 300 s becomes 300, a shorter one stays.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from faden_backend.db.meta import meta

    migration = _migration(OFF_SWITCH_MIGRATION, "0ffsw1t")
    assert (migration.revision, migration.down_revision) == ("0ffsw1t", "pl41nht")

    def columns(sync_conn: Any) -> set[str]:
        return {c["name"] for c in sa.inspect(sync_conn).get_columns("agents")}

    def stored(sync_conn: Any) -> Any:
        return sync_conn.execute(
            sa.text("SELECT value_json FROM system_setting_value WHERE key = 'agents.access_token_ttl_s'"),
        ).scalar_one()

    def run(sync_conn: Any) -> None:
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            migration.downgrade()
            assert not {"turned_off_at", "turned_off_by"} & columns(sync_conn)
            sync_conn.execute(sa.text(
                "INSERT INTO system_setting_value (key, value_json, version, updated_at) "
                "VALUES ('agents.access_token_ttl_s', CAST('{\"value\": 600}' AS jsonb), 1, now()) "
                "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json",
            ))
            migration.upgrade()
            assert {"turned_off_at", "turned_off_by"} <= columns(sync_conn)
            assert stored(sync_conn) == {"value": 300}
            diffs = [d for d in compare_metadata(context, meta) if "turned_off" in repr(d)]
            assert diffs == []

            migration.downgrade()
            sync_conn.execute(sa.text(
                "UPDATE system_setting_value SET value_json = CAST('{\"value\": 120}' AS jsonb) "
                "WHERE key = 'agents.access_token_ttl_s'",
            ))
            migration.upgrade()
            assert stored(sync_conn) == {"value": 120}, "a shorter one stays"

    connection = await dbsession.connection()
    await connection.run_sync(run)


# -- the device block, per device ---------------------------------------------------------------------

_INVENTORY = """
CREATE TABLE agent_device_inventory (
    agent_id varchar(64) NOT NULL, device_id varchar(64) NOT NULL, tenant_id varchar(128) NOT NULL,
    user_id varchar(128) NOT NULL, os varchar(100), app_version varchar(50), plugins json NOT NULL,
    mcp_programs json NOT NULL, reported_at timestamptz NOT NULL, PRIMARY KEY (agent_id, device_id)
)"""


@pytest.fixture
async def inventory(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    """The gateway's device table on the test's connection; a function to fill it."""
    await dbsession.execute(text(_INVENTORY))
    saved = getattr(fastapi_app.state, "llm_graph_trace_session_factory", None)
    fastapi_app.state.llm_graph_trace_session_factory = async_sessionmaker(dbsession.bind, expire_on_commit=False)

    async def report(agent_id: str, device_id: str, user_id: str, **block: Any) -> None:
        await dbsession.execute(
            text("""INSERT INTO agent_device_inventory (agent_id, device_id, tenant_id, user_id, os, app_version,
                    plugins, mcp_programs, reported_at)
                    VALUES (:agent, :device, 'default', :user, :os, :version, CAST(:plugins AS json),
                    CAST(:mcp AS json), :at)"""),
            {"agent": agent_id, "device": device_id, "user": user_id, "os": block.get("os"),
             "version": block.get("app_version"), "plugins": json.dumps(block.get("plugins", [])),
             "mcp": json.dumps(block.get("mcp_programs", [])), "at": datetime.now(UTC)},
        )

    yield report
    fastapi_app.state.llm_graph_trace_session_factory = saved


async def test_each_sign_in_shows_what_its_device_reported(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str, inventory: Any,  # noqa: F811
) -> None:
    signed_in.user = admin = await _user(dbsession, admin=True)
    user = await _user(dbsession)
    agent = await _agent(client, slug="inventoried")
    other = await _agent(client, slug="other-agent")
    await _grant(client, "inventoried", ("user", user.id))
    signed_in.user = user
    reported = (await _sign_in(client, "inventoried", "laptop")).json()
    silent = (await _sign_in(client, "inventoried", "desktop")).json()
    plugin = {"name": "contoso-crm", "version": "1.0.0", "sha256": "a" * 64}
    program = {"name": "files", "program": "server-filesystem.exe", "sha256": "b" * 64}
    await inventory(agent["id"], reported["device_id"], str(user.id), os="Windows 11", app_version="0.3.0",
                    plugins=[plugin], mcp_programs=[program])
    # The same device id under another agent is not this sign-in's.
    await inventory(other["id"], silent["device_id"], str(user.id), os="elsewhere")

    mine = {s["device_label"]: s for s in (await client.get("/api/agent-tokens/mine")).json()}
    device = mine["laptop"]["device"]
    assert (device["os"], device["app_version"]) == ("Windows 11", "0.3.0")
    assert device["plugins"] == [plugin] and device["mcp_programs"] == [program]
    assert device["reported_at"] is not None
    assert mine["desktop"]["device"] is None

    signed_in.user = admin
    listed = {s["device_label"]: s for s in (await client.get("/api/agents/inventoried/sign-ins")).json()}
    assert listed["laptop"]["device"] == device and listed["desktop"]["device"] is None


async def test_without_the_gateways_table_the_sign_ins_come_without_devices() -> None:
    """A gateway not upgraded yet has no device table: no devices, not an error; no gateway database: the same."""
    from types import SimpleNamespace

    from sqlalchemy.exc import ProgrammingError

    from faden_backend.web.api.agents import views

    class _OlderGateway:
        async def __aenter__(self) -> _OlderGateway:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            raise ProgrammingError("SELECT", {}, Exception('relation "agent_device_inventory" does not exist'))

    rows = [SimpleNamespace(family_id=uuid.uuid4(), agent_id=uuid.uuid4())]
    older = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm_graph_trace_session_factory=_OlderGateway)))
    assert await views._devices(older, rows) == {}  # type: ignore[arg-type]
    none = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert await views._devices(none, rows) == {}  # type: ignore[arg-type]


def test_the_backend_signs_the_call_that_ends_an_agents_grants(secret: str) -> None:  # noqa: F811
    token = gateway_client.agent_switch_grant("agent-1", secret=secret)
    claims = jwt.decode(token, secret, algorithms=["HS256"])
    assert (claims["typ"], claims["agent"]) == ("agent_switch", "agent-1")
    assert claims["exp"] - claims["iat"] == 60
    assert uuid.UUID(hex=claims["jti"])


def test_the_migration_file_is_where_the_test_looks() -> None:
    assert Path(OFF_SWITCH_MIGRATION).is_file()
    assert SystemSettingValue.__tablename__ == "system_setting_value"
