"""Fixtures: a real Postgres, made fresh from the migrations for the run.

The service is JSONB, row locks and SQL visibility rules; a fake database
would test none of them. The database is ``llm_skills_test`` (pytest-env in
pyproject); the connection comes from the usual ``LLM_PORT_SKILLS_DB_*``
variables or ``.env``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from llm_port_skills.settings import settings

ROOT = Path(__file__).resolve().parents[1]
SERVICE_TOKEN = "test-service-token"


def admin_dsn() -> str:
    """A connection to the server's ``postgres`` database, to make others."""
    return (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/postgres"
    )


async def recreate_database(name: str) -> None:
    """Drop *name* if it is there and create it empty."""
    conn = await asyncpg.connect(admin_dsn())
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def drop_database(name: str) -> None:
    """Drop *name*."""
    conn = await asyncpg.connect(admin_dsn())
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def alembic_config() -> Config:
    """The service's Alembic configuration, wherever pytest runs from."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "llm_port_skills/db/migrations"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    assert settings.db_base.endswith("_test"), settings.db_base
    asyncio.run(recreate_database(settings.db_base))
    command.upgrade(alembic_config(), "head")
    yield
    asyncio.run(drop_database(settings.db_base))


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    """Each test starts with no skills; the pool is closed with its loop."""
    yield
    from llm_port_skills.db.session import engine  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE skills, skill_versions, skill_grants, "
                "skill_assignments, skill_usage_daily CASCADE",
            ),
        )
    await engine.dispose()


@pytest.fixture
def app() -> FastAPI:
    """The application."""
    from llm_port_skills.web.application import get_app  # noqa: PLC0415

    return get_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client that calls the application in process, with the service token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        timeout=10.0,
    ) as ac:
        yield ac


def as_user(
    user_id: str,
    *,
    groups: tuple[str, ...] = (),
    caps: tuple[str, ...] = ("create", "share"),
    tenant: str | None = None,
) -> dict[str, str]:
    """The headers the backend sends for *user_id*."""
    headers = {
        "X-LLM-Port-User": user_id,
        "X-LLM-Port-Groups": ",".join(groups),
        "X-LLM-Port-Caps": ",".join(caps),
    }
    if tenant:
        headers["X-LLM-Port-Tenant"] = tenant
    return headers


ADMIN = as_user("admin-1", caps=("admin",))
