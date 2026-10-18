"""The container pages never hand out a secret value, and editing keeps the ones it hid."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from faden_backend.db.models.containers import AuditEvent, ContainerClass, ContainerPolicy
from faden_backend.db.models.users import current_active_user
from faden_backend.services.secret_redaction import MASK
from faden_backend.web.api.admin.dependencies import get_docker, get_root_mode_active
from tests.test_container_edit import IMAGE, NEW_ID, OLD_ID, _raw, _superuser

SECRET_ENV = [
    "PATH=/usr/local/bin:/usr/bin", "NODE_VERSION=22", "PORT=3001",
    "FADEN_GATEWAY_ENCRYPTION_KEY=the-chat-key",
    "DATABASE_URL=postgresql://postgres:pg-pass@postgres:5432/faden_gateway",
]
SECRET_CMD = ["redis-server", "--appendonly", "yes", "--requirepass", "redis-pass"]


def _secret_raw() -> dict[str, Any]:
    return _raw(Env=SECRET_ENV, Cmd=SECRET_CMD)


@pytest.fixture()
def docker() -> MagicMock:
    mock = MagicMock()
    mock.inspect_container = AsyncMock(side_effect=lambda cid: _secret_raw() if cid == OLD_ID else {
        **_secret_raw(), "Id": NEW_ID,
    })
    mock.inspect_image = AsyncMock(return_value=IMAGE)
    for name in ("rename", "connect_with_endpoint", "stop", "start", "delete"):
        setattr(mock, name, AsyncMock())
    mock.create_from_config = AsyncMock(return_value=NEW_ID)
    mock.create_container = AsyncMock(return_value=_secret_raw())
    return mock


@pytest.fixture()
async def app(fastapi_app: FastAPI, dbsession: AsyncSession, docker: MagicMock) -> FastAPI:
    user = await _superuser(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[get_docker] = lambda: docker
    # The gateway's settings are edited only in Root Mode (test_container_host_access.py).
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="faden-gateway", container_class=ContainerClass.SYSTEM_CORE,
        policy=ContainerPolicy.FREE,
    )
    return fastapi_app


@pytest.mark.anyio
async def test_the_detail_page_has_no_environment(app: FastAPI, client: AsyncClient, dbsession: AsyncSession) -> None:
    r = await client.get(app.url_path_for("get_container", container_id=OLD_ID))
    assert r.status_code == status.HTTP_200_OK, r.text
    assert "raw" not in r.json()
    for secret in ("the-chat-key", "pg-pass", "redis-pass"):
        assert secret not in r.text
    event = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "container.view", AuditEvent.target_id == OLD_ID),
    )).scalar_one()
    assert event.severity == "high"  # a system container: where the platform's keys are


@pytest.mark.anyio
async def test_the_edit_form_gets_secret_values_hidden(
    app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    r = await client.get(app.url_path_for("container_settings", container_id=OLD_ID))
    assert r.status_code == status.HTTP_200_OK, r.text
    body = r.json()
    for secret in ("the-chat-key", "pg-pass", "redis-pass"):
        assert secret not in r.text
    assert body["env"] == [
        "PORT=3001",
        f"FADEN_GATEWAY_ENCRYPTION_KEY={MASK}",
        f"DATABASE_URL=postgresql://postgres:{MASK}@postgres:5432/faden_gateway",
    ]
    assert body["hidden_env"] == ["FADEN_GATEWAY_ENCRYPTION_KEY", "DATABASE_URL"]
    assert body["cmd"] == ["redis-server", "--appendonly", "yes", "--requirepass", MASK]
    assert body["cmd_hidden"] is True
    assert (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "container.settings.view"),
    )).scalar_one().target_id == OLD_ID


@pytest.mark.anyio
async def test_saving_the_form_unchanged_keeps_the_secrets(
    app: FastAPI, client: AsyncClient, docker: MagicMock,
) -> None:
    form = (await client.get(app.url_path_for("container_settings", container_id=OLD_ID))).json()
    body = {
        "image": form["image"], "name": form["name"], "container_class": "SYSTEM_CORE", "owner_scope": "platform",
        "policy": "free", "auto_start": True, "ports": form["ports"], "env": [*form["env"], "LOG_LEVEL=debug"],
        "cmd": form["cmd"], "volumes": [], "network": form["network"],
    }
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=body)
    assert r.status_code == status.HTTP_200_OK, r.text
    config = docker.create_from_config.call_args.args[0]
    assert config["Env"] == [
        "PORT=3001",
        "FADEN_GATEWAY_ENCRYPTION_KEY=the-chat-key",
        "DATABASE_URL=postgresql://postgres:pg-pass@postgres:5432/faden_gateway",
        "LOG_LEVEL=debug",
    ]
    assert config["Cmd"] == SECRET_CMD


@pytest.mark.anyio
async def test_a_new_value_replaces_a_hidden_one(app: FastAPI, client: AsyncClient, docker: MagicMock) -> None:
    form = (await client.get(app.url_path_for("container_settings", container_id=OLD_ID))).json()
    env = ["FADEN_GATEWAY_ENCRYPTION_KEY=a-new-key" if e.startswith("FADEN_GATEWAY") else e for e in form["env"]]
    body = {
        "image": form["image"], "name": form["name"], "container_class": "SYSTEM_CORE", "owner_scope": "platform",
        "policy": "free", "env": env, "cmd": form["cmd"], "network": form["network"],
    }
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=body)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert "FADEN_GATEWAY_ENCRYPTION_KEY=a-new-key" in docker.create_from_config.call_args.args[0]["Env"]


@pytest.mark.anyio
async def test_a_hidden_value_under_a_new_name_is_refused(
    app: FastAPI, client: AsyncClient, docker: MagicMock,
) -> None:
    body = {
        "image": "node:22-alpine", "name": "faden-gateway", "container_class": "SYSTEM_CORE",
        "owner_scope": "platform", "policy": "free", "env": [f"OTHER_SECRET={MASK}"],
    }
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=body)
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    docker.create_from_config.assert_not_called()
