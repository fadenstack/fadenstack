"""Editing a container: it is replaced by one made from its settings and the edits.

Docker changes none of what the console edits in a container that exists,
and the console could not edit one at all: a running container could only be
deleted and made again by hand.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiodocker.exceptions import DockerError
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from llm_port_backend.db.models.containers import AuditEvent, ContainerClass, ContainerPolicy
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.docker import recreate
from llm_port_backend.web.api.admin.dependencies import get_docker

OLD_ID = "a" * 64
NEW_ID = "b" * 64

IMAGE = {
    "Id": "sha256:img",
    "Config": {
        "Env": ["PATH=/usr/local/bin:/usr/bin", "NODE_VERSION=22"],
        "Cmd": ["node"],
        "Entrypoint": ["docker-entrypoint.sh"],
        "WorkingDir": "/app",
        "Labels": {"maintainer": "node"},
    },
}


def _raw(**config: Any) -> dict[str, Any]:
    """An inspected container made from IMAGE, as Docker shows it: the image's settings merged in."""
    return {
        "Id": OLD_ID,
        "Name": "/mcp-everything",
        "Image": "sha256:img",
        "State": {"Running": True, "Status": "running"},
        "Config": {
            "Hostname": OLD_ID[:12],
            "Image": "node:22-alpine",
            "Env": ["PATH=/usr/local/bin:/usr/bin", "NODE_VERSION=22", "PORT=3001"],
            "Cmd": ["npx", "-y", "@modelcontextprotocol/server-everything", "streamableHttp"],
            "Entrypoint": ["docker-entrypoint.sh"],
            "WorkingDir": "/app",
            "Labels": {"maintainer": "node", "com.docker.compose.project": "llm_port_shared"},
            "Tty": False,
            **config,
        },
        "HostConfig": {
            "NetworkMode": "llm_port_shared_default",
            "PortBindings": {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3101"}]},
            "Binds": ["/srv/data:/data"],
            "RestartPolicy": {"Name": "unless-stopped"},
            "DeviceRequests": [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}],
        },
        "NetworkSettings": {"Networks": {
            "llm_port_shared_default": {"Aliases": ["mcp-everything", "everything", OLD_ID[:12]]},
            "monitoring": {"Aliases": ["mcp"], "IPAMConfig": {"IPv4Address": "172.30.0.9"}},
        }},
    }


# ── What the form shows, and what the new container is made of ───


def test_the_form_shows_what_the_container_set_not_its_image() -> None:
    own = recreate.own_settings(_raw(), IMAGE)
    assert own.env == ["PORT=3001"]
    assert own.cmd == ["npx", "-y", "@modelcontextprotocol/server-everything", "streamableHttp"]
    assert own.ports == {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3101"}]}
    assert (own.network, own.start, own.name) == ("llm_port_shared_default", True, "mcp-everything")


def test_a_command_the_image_supplies_is_not_shown_as_an_override() -> None:
    assert recreate.own_settings(_raw(Cmd=["node"]), IMAGE).cmd is None


def test_the_new_container_keeps_what_the_old_one_was_created_with() -> None:
    edit = recreate.own_settings(_raw(), IMAGE)
    edit.image, edit.env = "node:24-alpine", ["PORT=3001", "DEBUG=1"]
    config, _ = recreate.replacement(_raw(), IMAGE, edit)

    assert config["Image"] == "node:24-alpine"
    assert config["Env"] == ["PORT=3001", "DEBUG=1"]  # the new image brings PATH and its own
    for supplied in ("Entrypoint", "WorkingDir", "Hostname"):
        assert supplied not in config, supplied
    assert config["Labels"] == {"com.docker.compose.project": "llm_port_shared"}
    host = config["HostConfig"]
    assert host["RestartPolicy"] == {"Name": "unless-stopped"}
    assert host["DeviceRequests"][0]["Driver"] == "nvidia"
    assert host["PortBindings"] == {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3101"}]}


def test_an_entrypoint_the_container_set_is_kept() -> None:
    raw = _raw(Entrypoint=["/bin/sh", "-c"])
    config, _ = recreate.replacement(raw, IMAGE, recreate.own_settings(raw, IMAGE))
    assert config["Entrypoint"] == ["/bin/sh", "-c"]


def test_it_joins_its_networks_under_the_same_names() -> None:
    """Compose's aliases are how other containers reach it ("postgres"); its old id is not one."""
    config, others = recreate.replacement(_raw(), IMAGE, recreate.own_settings(_raw(), IMAGE))
    assert config["NetworkingConfig"] == {"EndpointsConfig": {
        "llm_port_shared_default": {"Aliases": ["mcp-everything", "everything"]},
    }}
    assert others == [("monitoring", {"Aliases": ["mcp"], "IPAMConfig": {"IPv4Address": "172.30.0.9"}})]


# ── The swap ──────────────────────────────────────────────────────


class _Docker:
    """Records what is done to which container; fails where it is told to."""

    def __init__(self, fail: str | None = None) -> None:
        self.done: list[str] = []
        self.fail = fail

    def _step(self, what: str) -> None:
        self.done.append(what)
        if what.split(" ")[0] == self.fail:
            raise DockerError(400, f"{self.fail} failed")

    async def rename(self, cid: str, name: str) -> None:
        self._step(f"rename {cid[:1]} {name}")

    async def create_from_config(self, config: dict[str, Any], *, name: str) -> str:
        self._step(f"create {name}")
        return NEW_ID

    async def connect_with_endpoint(self, network: str, cid: str, endpoint: dict[str, Any]) -> None:
        self._step(f"connect {cid[:1]} {network}")

    async def stop(self, cid: str) -> None:
        self._step(f"stop {cid[:1]}")

    async def start(self, cid: str) -> None:
        self._step(f"start {cid[:1]}")

    async def delete(self, cid: str, force: bool = False) -> None:
        self._step(f"delete {cid[:1]}")

    async def inspect_container(self, cid: str) -> dict[str, Any]:
        return {"Id": cid}


async def _replace(docker: _Docker) -> dict[str, Any]:
    raw = _raw()
    config, others = recreate.replacement(raw, IMAGE, recreate.own_settings(raw, IMAGE))
    return await recreate.replace(docker, raw, config, others, name="mcp-everything", start=True)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_the_old_container_steps_aside_until_the_new_one_runs() -> None:
    docker = _Docker()
    assert (await _replace(docker))["Id"] == NEW_ID
    assert docker.done == [
        "rename a mcp-everything-replaced-aaaaaaaa",
        "create mcp-everything",
        "connect b monitoring",
        "stop a",
        "start b",
        "delete a",
    ]


@pytest.mark.anyio
async def test_a_new_container_that_cannot_be_made_leaves_the_old_one_running() -> None:
    """A missing image, a name in use: the old one was never stopped."""
    docker = _Docker(fail="create")
    with pytest.raises(DockerError):
        await _replace(docker)
    assert docker.done[-1] == "rename a mcp-everything"
    assert not any(step.startswith("stop") for step in docker.done)


@pytest.mark.anyio
async def test_a_new_container_that_cannot_start_puts_the_old_one_back() -> None:
    """A port taken, a mount that does not exist: removed, and the old one runs again."""
    docker = _Docker(fail="start")
    with pytest.raises(DockerError):
        await _replace(docker)
    assert docker.done[-4:] == ["start b", "delete b", "rename a mcp-everything", "start a"]


def test_the_backend_knows_the_container_it_runs_in(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"612 598 8:1 /var/lib/docker/containers/{OLD_ID}/hostname /etc/hostname rw - ext4 /dev/sda1 rw\n",
    )
    assert recreate.own_container_id(mountinfo) == OLD_ID
    assert recreate.own_container_id(tmp_path / "missing") is None


# ── The API ───────────────────────────────────────────────────────


async def _superuser(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(), email=f"root-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",
        is_active=True, is_superuser=True, is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture()
def docker() -> MagicMock:
    mock = MagicMock()
    mock.inspect_container = AsyncMock(side_effect=lambda cid: _raw() if cid == OLD_ID else {
        **_raw(), "Id": NEW_ID, "State": {"Running": True, "Status": "running"},
    })
    mock.inspect_image = AsyncMock(return_value=IMAGE)
    for name in ("rename", "connect_with_endpoint", "stop", "start", "delete"):
        setattr(mock, name, AsyncMock())
    mock.create_from_config = AsyncMock(return_value=NEW_ID)
    return mock


@pytest.fixture()
async def app(fastapi_app: FastAPI, dbsession: AsyncSession, docker: MagicMock) -> FastAPI:
    user = await _superuser(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[get_docker] = lambda: docker
    return fastapi_app


def _edited(**changes: Any) -> dict[str, Any]:
    return {
        "image": "node:22-alpine", "name": "mcp-everything", "container_class": "MCP",
        "owner_scope": "platform", "policy": "free", "auto_start": True,
        "ports": [{"host_port": "3101", "container_port": "3001/tcp"}],
        "env": ["PORT=3001", "DEBUG=1"], "volumes": ["/srv/data:/data"],
        "network": "llm_port_shared_default", **changes,
    }


@pytest.mark.anyio
async def test_the_edit_form_gets_what_the_container_set(app: FastAPI, client: AsyncClient) -> None:
    r = await client.get(app.url_path_for("container_settings", container_id=OLD_ID))
    assert r.status_code == status.HTTP_200_OK
    body = r.json()
    assert body["env"] == ["PORT=3001"]
    assert body["ports"] == [{"host_port": "3101", "container_port": "3001/tcp"}]
    assert (body["managed_by_compose"], body["backend_runs_here"]) == (True, False)


@pytest.mark.anyio
async def test_an_mcp_container_is_edited_and_keeps_its_class_under_its_new_id(
    app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="mcp-everything", container_class=ContainerClass.MCP,
        policy=ContainerPolicy.FREE,
    )
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=_edited())
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["id"] == NEW_ID
    config = docker.create_from_config.call_args.args[0]
    assert config["Env"] == ["PORT=3001", "DEBUG=1"]
    # The host address the binding had is kept: the form does not show it.
    assert config["HostConfig"]["PortBindings"] == {"3001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3101"}]}

    registry = ContainerRegistryDAO(dbsession)
    assert await registry.get(OLD_ID) is None
    moved = await registry.get(NEW_ID)
    assert moved is not None and moved.container_class == ContainerClass.MCP
    event = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "container.update", AuditEvent.target_id == NEW_ID),
    )).scalar_one()
    assert json.loads(event.metadata_json)["env"] == ["DEBUG", "PORT"]  # names, not values


@pytest.mark.anyio
async def test_a_locked_container_is_not_edited(
    app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="mcp-everything", container_class=ContainerClass.TENANT_APP,
        policy=ContainerPolicy.LOCKED,
    )
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=_edited())
    assert r.status_code == status.HTTP_403_FORBIDDEN
    docker.create_from_config.assert_not_called()


@pytest.mark.anyio
async def test_the_backend_does_not_replace_the_container_it_runs_in(
    app: FastAPI, client: AsyncClient, docker: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It would stop itself half way, with the old container stopped and no new one."""
    monkeypatch.setattr(recreate, "own_container_id", lambda: OLD_ID)
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=_edited())
    assert r.status_code == status.HTTP_409_CONFLICT
    assert "llmport upgrade" in r.json()["detail"]
    docker.rename.assert_not_called()


@pytest.mark.anyio
async def test_what_docker_refuses_is_said(app: FastAPI, client: AsyncClient, docker: MagicMock) -> None:
    docker.create_from_config = AsyncMock(side_effect=DockerError(404, "No such image: node:99"))
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=_edited(image="node:99"))
    assert r.status_code == status.HTTP_404_NOT_FOUND
    assert r.json()["detail"] == "The container was not changed: No such image: node:99"


@pytest.mark.anyio
async def test_deleting_a_running_container_says_to_stop_it(
    app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """It answered 500: Docker's 409 was not mapped."""
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="demo", container_class=ContainerClass.TENANT_APP, policy=ContainerPolicy.FREE,
    )
    docker.delete = AsyncMock(side_effect=DockerError(409, "cannot remove container: it is running"))
    r = await client.delete(app.url_path_for("delete_container", container_id=OLD_ID))
    assert r.status_code == status.HTTP_409_CONFLICT
    assert r.json()["detail"] == "The container is running: stop it before you delete it."



def test_a_named_volume_stays_a_volume() -> None:
    """Resolved as a path, Loki's volume became a folder under the backend's working directory."""
    from llm_port_backend.services.docker.client import resolve_binds  # noqa: PLC0415

    assert resolve_binds(["llm_port_shared_loki_data:/loki:rw"]) == ["llm_port_shared_loki_data:/loki:rw"]
    (drive,) = resolve_binds(["C:/models:/models"])  # a Windows path, not a volume named "C"
    assert drive.endswith(":/models")
    assert drive[1] == ":"
