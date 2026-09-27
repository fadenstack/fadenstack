"""Migration 002 on data made by the old service."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import pytest
from alembic import command

from llm_port_skills.services import bundle as fmt
from llm_port_skills.settings import settings
from tests.conftest import alembic_config, drop_database, recreate_database

DB = "llm_skills_migration_test"
A = uuid.uuid4()
B = uuid.uuid4()
C = uuid.uuid4()


def _dsn() -> str:
    return (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/{DB}"
    )


async def _insert_legacy() -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        insert_skill = """
            INSERT INTO skills (id, tenant_id, name, slug, description, scope, status,
                                enabled, priority, tags, allowed_tools, trigger_rules,
                                current_version, created_by)
            VALUES ($1, 'default', $2, $3, $4, $5, $6, true, 50, '[]'::jsonb,
                    $7::jsonb, $8::jsonb, $9, 'service')
        """
        await conn.execute(
            insert_skill,
            A,
            "Analysis",
            "analysis",
            "row desc",
            "global",
            "published",
            json.dumps(["a"]),
            json.dumps({"keywords": ["x"]}),
            3,
        )
        await conn.execute(
            insert_skill,
            B,
            "Draft",
            "draft",
            "",
            "workspace",
            "draft",
            None,
            None,
            1,
        )
        await conn.execute(
            insert_skill,
            C,
            "Old",
            "old",
            "",
            "user",
            "archived",
            None,
            None,
            1,
        )
        insert_version = """
            INSERT INTO skill_versions
                (id, skill_id, version, body_markdown, frontmatter_yaml)
            VALUES ($1, $2, $3, $4, $5)
        """
        # v1 of A was pruned by the old five-version limit.
        await conn.execute(
            insert_version,
            uuid.uuid4(),
            A,
            2,
            "old body",
            "name: Analysis\ndescription: old desc\nscope: global\n"
            "allowed_tools: [old]\nlicense: MIT\n",
        )
        await conn.execute(
            insert_version,
            uuid.uuid4(),
            A,
            3,
            "new body",
            "name: Analysis\ndescription: stale\nallowed_tools: [stale]\n",
        )
        await conn.execute(insert_version, uuid.uuid4(), B, 1, "b", None)
        await conn.execute(insert_version, uuid.uuid4(), C, 1, "c", "name: Old\n")
    finally:
        await conn.close()


async def _fetch(query: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(_dsn())
    try:
        return [dict(r) for r in await conn.fetch(query)]
    finally:
        await conn.close()


def _decoded(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (
            json.loads(v)
            if isinstance(v, str) and k in {"frontmatter", "manifest", "files"}
            else v
        )
        for k, v in row.items()
    }


def test_existing_skills_keep_behaving_as_they_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_base", DB)
    asyncio.run(recreate_database(DB))
    try:
        cfg = alembic_config()
        command.upgrade(cfg, "001_initial")
        asyncio.run(_insert_legacy())
        command.upgrade(cfg, "head")

        skills = {
            r["slug"]: r
            for r in asyncio.run(
                _fetch(
                    "SELECT slug, owner_id, visibility, activation, status, "
                    "latest_version, published_version FROM skills",
                ),
            )
        }
        # A global/tenant skill came into every chat; a workspace one only
        # when it matched. The organization owns them; the tenant sees them.
        assert skills["analysis"] == {
            "slug": "analysis",
            "owner_id": None,
            "visibility": "tenant",
            "activation": "always",
            "status": "published",
            "latest_version": 3,
            "published_version": 3,
        }
        assert skills["draft"]["activation"] == "auto"
        assert skills["draft"]["published_version"] is None
        assert skills["old"]["status"] == "archived"
        assert skills["old"]["published_version"] == 1  # restoring brings it back

        versions = {
            (r["slug"], r["version"]): _decoded(r)
            for r in asyncio.run(
                _fetch(
                    "SELECT s.slug, v.version, v.description, v.body_markdown, "
                    "v.frontmatter::text AS frontmatter, v.manifest::text AS manifest, "
                    "v.files::text AS files, v.content_sha256 "
                    "FROM skill_versions v JOIN skills s ON s.id = v.skill_id",
                ),
            )
        }
        old = versions[("analysis", 2)]
        # An older version keeps what its own frontmatter said, falling back
        # to the skill's fields for what it did not say.
        assert old["manifest"] == {
            "allowed_tools": ["old"],
            "trigger_rules": {"keywords": ["x"]},
        }
        assert old["description"] == "old desc"
        assert old["frontmatter"] == {"license": "MIT"}
        latest = versions[("analysis", 3)]
        # The latest one is what chats got: the skill's own fields, which
        # were edited without a new version.
        assert latest["manifest"] == {
            "allowed_tools": ["a"],
            "trigger_rules": {"keywords": ["x"]},
        }
        assert latest["description"] == "row desc"

        # The migration's copy of the hash agrees with the service's, so
        # saving unchanged content after the upgrade adds no version.
        for (slug, _), row in versions.items():
            assert row["content_sha256"] == fmt.content_sha256(
                name=slug,
                description=row["description"],
                body=row["body_markdown"],
                frontmatter=row["frontmatter"],
                manifest=row["manifest"],
                files=row["files"],
            )

        command.downgrade(cfg, "001_initial")
        back = {
            r["slug"]: r
            for r in asyncio.run(
                _fetch(
                    "SELECT slug, scope, current_version, "
                    "allowed_tools::text AS allowed FROM skills",
                ),
            )
        }
        assert back["analysis"]["scope"] == "tenant"
        assert back["analysis"]["current_version"] == 3
        assert json.loads(back["analysis"]["allowed"]) == ["a"]
        assert back["draft"]["scope"] == "workspace"
    finally:
        asyncio.run(drop_database(DB))
