"""Stacks, providers and clusters hide secret values from the console and keep them on save."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.llm_dao import ProviderDAO
from faden_backend.db.dao.stacks_dao import StackRevisionDAO
from faden_backend.db.models.llm import ProviderTarget, ProviderType
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.llm.service import LLMService
from faden_backend.services.secret_redaction import MASK
from faden_backend.web.api.inference.environments import _dto_from_env, _restored_config
from faden_backend.web.api.llm.dependencies import get_llm_service

ENV_BLOB = "# app\nPORT=80\nAPI_TOKEN=stack-token\nDB_URL=postgres://app:db-pass@db/app\n"


@pytest.fixture
async def superuser(dbsession: AsyncSession, fastapi_app: FastAPI) -> User:
    user = User(
        id=uuid.uuid4(), email=f"root-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",
        is_active=True, is_verified=True, is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    service = LLMService(SimpleNamespace(), gateway_sync=GatewaySyncService(None))  # type: ignore[arg-type]
    fastapi_app.dependency_overrides[get_llm_service] = lambda: service
    return user


# ── Stacks ─────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_stack_revisions_and_diffs_hide_env_values(
    client: AsyncClient, dbsession: AsyncSession, superuser: User,
) -> None:
    dao = StackRevisionDAO(dbsession)
    await dao.create(stack_id="app", compose_yaml="services: {}", env_blob=ENV_BLOB)
    await dao.create(stack_id="app", compose_yaml="services: {}", env_blob=ENV_BLOB.replace("80", "81"))
    revisions = await client.get("/api/admin/stacks/app/revisions")
    assert revisions.status_code == status.HTTP_200_OK, revisions.text
    diff = await client.get("/api/admin/stacks/app/diff", params={"from_rev": 1, "to_rev": 2})
    assert diff.status_code == status.HTTP_200_OK, diff.text
    for text in (revisions.text, diff.text):
        assert "stack-token" not in text and "db-pass" not in text
    assert revisions.json()[0]["env_blob"] == (
        f"# app\nPORT=81\nAPI_TOKEN={MASK}\nDB_URL=postgres://app:{MASK}@db/app\n"
    )


@pytest.mark.anyio
async def test_a_stack_update_with_hidden_values_keeps_the_previous_ones(
    client: AsyncClient, dbsession: AsyncSession, superuser: User,
) -> None:
    await StackRevisionDAO(dbsession).create(stack_id="app", compose_yaml="services: {}", env_blob=ENV_BLOB)
    shown = (await client.get("/api/admin/stacks/app/revisions")).json()[0]["env_blob"]
    r = await client.post(
        "/api/admin/stacks/app/update",
        json={"stack_id": "app", "compose_yaml": "services: {web: {}}", "env_blob": shown.replace("PORT=80", "PORT=8080")},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    latest = await StackRevisionDAO(dbsession).latest("app")
    assert latest is not None
    assert latest.env_blob == ENV_BLOB.replace("PORT=80", "PORT=8080")


# ── Providers ──────────────────────────────────────────────────────────────────────


async def _provider(dbsession: AsyncSession) -> uuid.UUID:
    provider = await ProviderDAO(session=dbsession).create(
        name="remote", type_=ProviderType.CLOUD, target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="https://llm.example.com/v1", capabilities={},
        extra_params={"extra_headers": {"Authorization": "Bearer header-key", "X-Region": "eu"}},
    )
    return provider.id


@pytest.mark.anyio
async def test_provider_extra_headers_are_hidden_and_kept(
    client: AsyncClient, dbsession: AsyncSession, superuser: User,
) -> None:
    pid = await _provider(dbsession)
    shown = await client.get(f"/api/llm/providers/{pid}")
    assert shown.status_code == status.HTTP_200_OK, shown.text
    assert "header-key" not in shown.text
    extra = shown.json()["extra_params"]
    assert extra == {"extra_headers": {"Authorization": MASK, "X-Region": "eu"}}
    extra["extra_headers"]["X-Region"] = "de"
    r = await client.patch(f"/api/llm/providers/{pid}", json={"extra_params": extra})
    assert r.status_code == status.HTTP_200_OK, r.text
    stored = await ProviderDAO(session=dbsession).get(pid)
    assert stored is not None
    assert stored.extra_params == {"extra_headers": {"Authorization": "Bearer header-key", "X-Region": "de"}}


# ── Cluster (inference environment) node environment ───────────────────────────────


def _env(config: dict) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(), control_plane_id=uuid.uuid4(), name="c", description=None, status="ready",
        desired_state="running", runtime_version=None, head_node_id=None, address=None, config_json=config,
        capabilities_json={}, observed_status_json={}, generation=1, observed_generation=1, status_message=None,
        created_at=now, updated_at=now,
    )


def test_node_env_values_are_hidden_and_kept() -> None:
    stored = {"head_port": 6379, "node_env_vars": {"HF_TOKEN": "hf-secret", "NCCL_DEBUG": "INFO"}}
    dto = _dto_from_env(_env(stored))
    assert dto.config["node_env_vars"] == {"HF_TOKEN": MASK, "NCCL_DEBUG": "INFO"}
    assert dto.config["head_port"] == 6379
    sent = {**dto.config, "node_env_vars": {**dto.config["node_env_vars"], "NCCL_DEBUG": "WARN"}}
    assert _restored_config(sent, stored)["node_env_vars"] == {"HF_TOKEN": "hf-secret", "NCCL_DEBUG": "WARN"}
    with pytest.raises(HTTPException):
        _restored_config({"node_env_vars": {"OTHER_TOKEN": MASK}}, stored)
