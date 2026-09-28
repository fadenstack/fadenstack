"""The agent directory's query, on the backend's real schema.

Runs against a backend database migrated to head, named by
``FADEN_TEST_BACKEND_DSN`` (``postgresql://user:pass@host:5432/db``); skipped
without one. It writes its own rows and removes them.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from faden_gateway.services.gateway.agents import BackendAgents

DSN = os.environ.get("FADEN_TEST_BACKEND_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="FADEN_TEST_BACKEND_DSN names no backend database")


@pytest.fixture
async def rows() -> AsyncIterator[dict[str, str]]:
    conn = await asyncpg.connect(DSN)
    ids = {k: str(uuid.uuid4()) for k in ("user", "maintainer", "group", "agent", "v1", "v2", "draft")}
    slug = f"dir-{uuid.uuid4().hex[:8]}"
    try:
        for key in ("user", "maintainer"):
            await conn.execute(
                'INSERT INTO "user" (id, email, hashed_password, is_active, is_superuser, is_verified) '
                "VALUES ($1::uuid, $2, 'x', true, false, true)",
                ids[key], f"{ids[key]}@example.com",
            )
        await conn.execute("INSERT INTO groups (id, name) VALUES ($1::uuid, $2)", ids["group"], f"team-{slug}")
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, slug, name, status) VALUES ($1::uuid, 'default', $2, 'Dir', 'draft')",
            ids["agent"], slug,
        )
        for key, number, text in (("v1", 1, "First."), ("v2", 2, "Second.")):
            await conn.execute(
                "INSERT INTO agent_versions (id, agent_id, version, instructions, model_alias, knowledge, "
                "server_tools, host_tool_contract, ui, extensions) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, 'qwen3-32b', '[\"kb-1\"]', '[\"knowledge_*\"]', "
                "$5::jsonb, '{\"greeting\": \"Hi\"}', '{}')",
                ids[key], ids["agent"], number, text, json.dumps([{"name": "read_page"}]),
            )
        await conn.execute(
            "INSERT INTO agent_members (agent_id, user_id, role) VALUES ($1::uuid, $2::uuid, 'maintainer')",
            ids["agent"], ids["maintainer"],
        )
        await conn.execute(
            "INSERT INTO agent_grants (id, agent_id, principal_type, principal_id) "
            "VALUES (gen_random_uuid(), $1::uuid, 'user', $2::uuid), (gen_random_uuid(), $1::uuid, 'group', $3::uuid)",
            ids["agent"], ids["user"], ids["group"],
        )
        yield {**ids, "slug": slug, "dsn": DSN}
    finally:
        await conn.execute("DELETE FROM agents WHERE id = $1::uuid", ids["agent"])
        await conn.execute("DELETE FROM groups WHERE id = $1::uuid", ids["group"])
        await conn.execute('DELETE FROM "user" WHERE id = ANY($1::uuid[])', [ids["user"], ids["maintainer"]])
        await conn.close()


@pytest.mark.anyio
async def test_a_draft_serves_its_newest_version_a_published_agent_its_published_one(
    rows: dict[str, str],
) -> None:
    backend = BackendAgents(rows["dsn"])
    try:
        draft = await backend.agent(("default", rows["slug"]))
        assert draft is not None
        assert (draft.published, draft.version, draft.instructions) == (False, 2, "Second.")
        assert draft.maintainers == {rows["maintainer"]}
        assert draft.grant_users == {rows["user"]} and draft.grant_groups == {rows["group"]}
        assert draft.knowledge == ("kb-1",) and draft.server_tools == ("knowledge_*",)
        assert draft.host_tools == {"read_page"} and draft.ui == {"greeting": "Hi"}

        conn = await asyncpg.connect(rows["dsn"])
        await conn.execute(
            "UPDATE agents SET status = 'published', published_version_id = $1::uuid WHERE id = $2::uuid",
            rows["v1"], rows["agent"],
        )
        await conn.close()
        published = await backend.agent(("default", rows["agent"]))  # by id this time
        assert published is not None
        assert (published.published, published.version, published.instructions) == (True, 1, "First.")
        assert published.status == "published"

        assert await backend.agent(("other-tenant", rows["slug"])) is None
        flags = await backend.user(rows["user"])
        assert flags is not None and flags.is_active and not flags.is_superuser
        assert await backend.user("not-a-uuid") is None
    finally:
        await backend.close()
