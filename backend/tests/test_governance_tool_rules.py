"""Tool rules for admins: deny or "Ask first"; no new allow rules, and older ones listed as having no effect.

``llm_gateway_tool_rule`` lives in the gateway's database; here it is made in
the test database, on the test's connection, and goes with its rollback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from faden_backend.db.models.users import User, current_active_user

#: The gateway's table, as its migrations leave it.
_RULE_TABLE = """
CREATE TABLE llm_gateway_tool_rule (
    id uuid PRIMARY KEY, tenant_id varchar(128) NOT NULL, name varchar(256) NOT NULL,
    tool_pattern varchar(256) NOT NULL, action varchar(32) NOT NULL, message text NOT NULL,
    category varchar(64) NOT NULL, priority integer NOT NULL, enabled boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
)"""

DENY = {
    "name": "No GitHub deletes", "tool_pattern": "mcp.github.delete_*", "action": "deny",
    "message": "Deleting on GitHub is not allowed here.", "priority": 10,
}


@pytest.fixture
async def gateway_db(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[AsyncSession]:
    await dbsession.execute(text(_RULE_TABLE))
    saved = getattr(fastapi_app.state, "llm_graph_trace_session_factory", None)
    fastapi_app.state.llm_graph_trace_session_factory = async_sessionmaker(dbsession.bind, expire_on_commit=False)
    yield dbsession
    fastapi_app.state.llm_graph_trace_session_factory = saved


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[User]:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:10]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    yield user
    fastapi_app.dependency_overrides.pop(current_active_user, None)


async def _old_allow_rule(db: AsyncSession) -> str:
    """An allow rule as an earlier version saved it."""
    rule_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO llm_gateway_tool_rule (id, tenant_id, name, tool_pattern, action, message, category, "
            "priority, enabled) VALUES (:id, 'default', 'GitHub is fine', 'mcp.github.*', 'allow', "
            "'GitHub tools may run.', 'tool_rule', 5, true)",
        ),
        {"id": rule_id},
    )
    return str(rule_id)


async def _actions(db: AsyncSession) -> list[str]:
    rows = await db.execute(text("SELECT action FROM llm_gateway_tool_rule ORDER BY priority"))
    return [r.action for r in rows]


@pytest.mark.parametrize("action", ["deny", "approval_required"])
async def test_a_rule_denies_or_asks_first(
    client: AsyncClient, gateway_db: AsyncSession, admin: User, action: str,
) -> None:
    r = await client.post("/api/admin/governance/tool-rules", json={**DENY, "action": action})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert (r.json()["action"], r.json()["no_effect"]) == (action, False)


async def test_a_new_allow_rule_is_refused_and_says_why(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    r = await client.post("/api/admin/governance/tool-rules", json={**DENY, "action": "allow"})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert "would change nothing" in r.text
    assert await _actions(gateway_db) == []


async def test_a_rule_cannot_be_changed_to_allow(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    created = (await client.post("/api/admin/governance/tool-rules", json=DENY)).json()

    r = await client.put(f"/api/admin/governance/tool-rules/{created['id']}", json={**DENY, "action": "allow"})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert await _actions(gateway_db) == ["deny"]


async def test_an_older_allow_rule_is_listed_as_having_no_effect(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    await _old_allow_rule(gateway_db)
    await client.post("/api/admin/governance/tool-rules", json=DENY)

    r = await client.get("/api/admin/governance/tool-rules")
    assert r.status_code == status.HTTP_200_OK, r.text
    assert [(x["action"], x["no_effect"]) for x in r.json()["data"]] == [("allow", True), ("deny", False)]


async def test_an_older_allow_rule_can_become_ask_first_or_go(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    rule_id = await _old_allow_rule(gateway_db)

    r = await client.put(
        f"/api/admin/governance/tool-rules/{rule_id}",
        json={**DENY, "tool_pattern": "mcp.github.*", "action": "approval_required"},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    assert (r.json()["action"], r.json()["no_effect"]) == ("approval_required", False)

    r = await client.delete(f"/api/admin/governance/tool-rules/{rule_id}")
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    assert await _actions(gateway_db) == []
