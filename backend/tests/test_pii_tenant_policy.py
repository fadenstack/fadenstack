"""The tenant PII policy page: its policy override, and whether chats and agents may change the PII mode.

``tenant_llm_policy`` lives in the gateway's database; here it is made in the
test database, on the test's connection, and goes with its rollback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from faden_backend.db.models.users import User, current_active_user

#: The gateway's table, as its migrations leave it (the columns these endpoints touch, and their defaults).
_POLICY_TABLE = """
CREATE TABLE tenant_llm_policy (
    tenant_id varchar(128) PRIMARY KEY,
    privacy_mode varchar(32) NOT NULL DEFAULT 'metadata_only',
    allowed_model_aliases json, allowed_provider_types json, rpm_limit integer, tpm_limit integer,
    pii_config json,
    allow_mode_override boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
)"""

POLICY = {
    "telemetry": {"enabled": False, "mode": "sanitized", "store_raw": False},
    "egress": {"enabled_for_cloud": True, "enabled_for_local": False, "mode": "redact", "fail_action": "block"},
    "presidio": {"language": "en", "threshold": 0.6, "entities": ["PERSON"]},
}


@pytest.fixture
async def gateway_db(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[AsyncSession]:
    await dbsession.execute(text(_POLICY_TABLE))
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


async def _row(db: AsyncSession, tenant: str) -> Any:
    return (await db.execute(
        text("SELECT pii_config, allow_mode_override FROM tenant_llm_policy WHERE tenant_id = :t"), {"t": tenant},
    )).first()


async def test_a_tenant_without_a_row_cannot_change_the_mode(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    r = await client.get("/api/admin/pii/policies/tenants/acme")
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert body["allow_mode_override"] is False
    assert body["has_override"] is False


async def test_the_switch_is_saved_on_its_own_without_writing_a_policy(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    r = await client.put("/api/admin/pii/policies/tenants/acme", json={"allow_mode_override": True})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["allow_mode_override"] is True
    assert r.json()["has_override"] is False, "no policy override was written"
    assert tuple(await _row(gateway_db, "acme")) == (None, True)

    r = await client.get("/api/admin/pii/policies/tenants/acme")
    assert r.json()["allow_mode_override"] is True


async def test_saving_the_policy_keeps_the_switch_and_clearing_it_too(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    await client.put("/api/admin/pii/policies/tenants/acme", json={"allow_mode_override": True})

    r = await client.put("/api/admin/pii/policies/tenants/acme", json={"pii_config": POLICY})
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    assert (body["has_override"], body["override_policy"], body["allow_mode_override"]) == (True, POLICY, True)

    r = await client.put(
        "/api/admin/pii/policies/tenants/acme", json={"pii_config": POLICY, "allow_mode_override": False},
    )
    assert r.json()["allow_mode_override"] is False
    assert tuple(await _row(gateway_db, "acme")) == (POLICY, False)

    await client.put("/api/admin/pii/policies/tenants/acme", json={"allow_mode_override": True})
    r = await client.delete("/api/admin/pii/policies/tenants/acme")
    assert r.status_code == status.HTTP_200_OK, r.text
    assert (r.json()["has_override"], r.json()["allow_mode_override"]) == (False, True)
    assert tuple(await _row(gateway_db, "acme")) == (None, True)


async def test_a_new_tenant_saved_with_a_policy_starts_with_the_switch_off(
    client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    r = await client.put("/api/admin/pii/policies/tenants/fresh", json={"pii_config": POLICY})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["allow_mode_override"] is False
    assert tuple(await _row(gateway_db, "fresh")) == (POLICY, False)


@pytest.mark.parametrize("value", ["yes", 1, "true"])
async def test_the_switch_takes_only_true_or_false(
    value: Any, client: AsyncClient, gateway_db: AsyncSession, admin: User,
) -> None:
    r = await client.put("/api/admin/pii/policies/tenants/acme", json={"allow_mode_override": value})
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_only_superusers_change_it(
    fastapi_app: FastAPI, client: AsyncClient, gateway_db: AsyncSession, dbsession: AsyncSession,
) -> None:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:10]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=False,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    try:
        r = await client.put("/api/admin/pii/policies/tenants/acme", json={"allow_mode_override": True})
        assert r.status_code == status.HTTP_403_FORBIDDEN
        assert await _row(gateway_db, "acme") is None
    finally:
        fastapi_app.dependency_overrides.pop(current_active_user, None)
