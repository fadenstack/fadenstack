"""Containers with root on the host: superusers only, and only in Root Mode.

The delegable admin role could make any container through the console API,
whatever it bound and whichever network it joined; edit and register needed
only ``containers:update``, and every lifecycle action only ``containers:start``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.containers import ContainerClass, ContainerPolicy
from faden_backend.db.models.users import User, current_active_user
from faden_backend.web.api.admin.containers import host_access
from faden_backend.web.api.admin.dependencies import get_docker, get_root_mode_active

OLD_ID = "c" * 64
NEW_ID = "d" * 64
SOCKET = "/var/run/docker.sock:/var/run/docker.sock:ro"

# ── What the check finds ──────────────────────────────────────────


@pytest.mark.parametrize(
    "bind",
    [
        SOCKET,
        "//var/run/docker.sock:/var/run/docker.sock:ro",  # the compose file's spelling for Docker Desktop
        "/:/host:ro",
        "/etc:/host-etc:ro",
        "/var:/host-var",  # holds /var/lib/docker and /var/run
        "/srv/../../:/host",
    ],
)
def test_a_bind_with_root_on_the_host_is_found(bind: str) -> None:
    assert host_access.bind_reasons([bind])


@pytest.mark.parametrize(
    "bind",
    [
        "/srv/data:/data",
        "/srv/faden/models:/models:ro",
        "fadenstack_loki_data:/loki:rw",  # a named volume
        "C:/models:/models",
        "/etc/localtime:/etc/localtime:ro",
    ],
)
def test_an_ordinary_bind_passes(bind: str) -> None:
    assert host_access.bind_reasons([bind]) == []


def test_the_time_zone_is_harmless_only_read_only() -> None:
    assert host_access.bind_reasons(["/etc/localtime:/etc/localtime"])


@pytest.mark.parametrize(("network", "found"), [
    ("host", True), ("container:abc", True), ("fadenstack_default", False), ("bridge", False), ("", False), (None, False),
])
def test_the_network_modes_that_share_the_host_are_found(network: str | None, found: bool) -> None:
    assert bool(host_access.network_reason(network)) is found


def test_what_an_edit_keeps_from_the_old_container_counts_too() -> None:
    assert host_access.kept_reasons({"Privileged": True})
    assert host_access.kept_reasons({"CapAdd": ["SYS_ADMIN"]})
    assert host_access.kept_reasons({"PidMode": "host"})
    assert host_access.kept_reasons({"Mounts": [{"Type": "bind", "Source": "/var/run/docker.sock"}]})
    # A GPU, the restart policy, a volume mount: kept as they were.
    assert host_access.kept_reasons({
        "DeviceRequests": [{"Driver": "nvidia", "Count": -1}], "RestartPolicy": {"Name": "unless-stopped"},
        "IpcMode": "host", "Mounts": [{"Type": "volume", "Source": "faden_models"}],
    }) == []


# ── The API ───────────────────────────────────────────────────────


async def _user(db: AsyncSession, *, superuser: bool, role: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_superuser=superuser, is_verified=True,
    )
    db.add(user)
    await db.flush()
    if role:
        rbac = RbacDAO(db)
        await rbac.seed_defaults()
        found = await rbac.get_role_by_name(role)
        assert found is not None
        await rbac.assign_role(user.id, found.id)
    return user


def _raw(**host: Any) -> dict[str, Any]:
    return {
        "Id": OLD_ID, "Name": "/tool", "Image": "sha256:img",
        "State": {"Running": True, "Status": "running"},
        "Config": {"Image": "node:22-alpine", "Env": [], "Labels": {}},
        "HostConfig": {"NetworkMode": "fadenstack_default", "Binds": ["/srv/data:/data"], **host},
        "NetworkSettings": {"Networks": {"fadenstack_default": {"Aliases": ["tool"]}}},
    }


@pytest.fixture()
def docker() -> MagicMock:
    mock = MagicMock()
    created = {
        "Id": NEW_ID, "Name": "/tool", "State": {"Status": "running"},
        "Config": {"Image": "node:22-alpine"}, "NetworkSettings": {"Networks": {}},
    }
    mock.create_container = AsyncMock(return_value=created)
    mock.inspect_container = AsyncMock(side_effect=lambda cid: _raw() if cid == OLD_ID else created)
    mock.inspect_image = AsyncMock(return_value={"Config": {}})
    mock.list_containers = AsyncMock(return_value=[])
    mock.create_from_config = AsyncMock(return_value=NEW_ID)
    for name in ("rename", "connect_with_endpoint", "start", "stop", "restart", "pause", "unpause", "delete"):
        setattr(mock, name, AsyncMock())
    return mock


def _as(app: FastAPI, user: User, docker: MagicMock, *, root_mode: bool | None = None) -> FastAPI:
    app.dependency_overrides[current_active_user] = lambda: user
    app.dependency_overrides[get_docker] = lambda: docker
    if root_mode is not None:
        app.dependency_overrides[get_root_mode_active] = lambda: root_mode
    return app


def _create(**changes: Any) -> dict[str, Any]:
    return {
        "image": "node:22-alpine", "name": "tool", "container_class": "TENANT_APP", "auto_start": True,
        "volumes": ["/srv/data:/data"], "network": "fadenstack_default", **changes,
    }


@pytest.mark.anyio
async def test_the_admin_role_does_not_create_containers(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    app = _as(fastapi_app, await _user(dbsession, superuser=False, role="admin"), docker)
    r = await client.post(app.url_path_for("create_container"), json=_create())
    assert r.status_code == status.HTTP_403_FORBIDDEN
    docker.create_container.assert_not_called()


@pytest.mark.anyio
async def test_a_superuser_creates_an_ordinary_container(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    r = await client.post(app.url_path_for("create_container"), json=_create())
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert docker.create_container.call_args.kwargs["volumes"] == ["/srv/data:/data"]


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [{"volumes": [SOCKET]}, {"volumes": ["/:/host"]}, {"network": "host"}])
async def test_root_on_the_host_needs_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock, changes: dict[str, Any],
) -> None:
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    r = await client.post(app.url_path_for("create_container"), json=_create(**changes))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "Activate Root Mode" in r.json()["detail"]
    docker.create_container.assert_not_called()


@pytest.mark.anyio
async def test_in_root_mode_a_superuser_binds_the_socket(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker, root_mode=True)
    r = await client.post(app.url_path_for("create_container"), json=_create(volumes=[SOCKET]))
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert docker.create_container.call_args.kwargs["volumes"] == [SOCKET]


async def _registered(db: AsyncSession, cls: ContainerClass = ContainerClass.TENANT_APP) -> None:
    await ContainerRegistryDAO(db).upsert(
        container_id=OLD_ID, name="tool", container_class=cls, policy=ContainerPolicy.FREE,
    )


@pytest.mark.anyio
async def test_an_edit_that_adds_the_socket_needs_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await _registered(dbsession)
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    url = app.url_path_for("update_container", container_id=OLD_ID)
    r = await client.put(url, json=_create(volumes=["/srv/data:/data", SOCKET]))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "/var/run/docker.sock" in r.json()["detail"]
    docker.create_from_config.assert_not_called()
    # The same edit without it goes through.
    r = await client.put(url, json=_create())
    assert r.status_code == status.HTTP_200_OK, r.text


@pytest.mark.anyio
async def test_a_privileged_container_is_changed_only_in_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """The edit keeps what the form does not show: privileged with a new image is root."""
    await _registered(dbsession)
    docker.inspect_container = AsyncMock(side_effect=lambda cid: _raw(Privileged=True) if cid == OLD_ID else {
        **_raw(), "Id": NEW_ID,
    })
    user = await _user(dbsession, superuser=True)
    url = fastapi_app.url_path_for("update_container", container_id=OLD_ID)
    _as(fastapi_app, user, docker)
    r = await client.put(url, json=_create())
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "runs privileged" in r.json()["detail"]
    _as(fastapi_app, user, docker, root_mode=True)
    r = await client.put(url, json=_create())
    assert r.status_code == status.HTTP_200_OK, r.text


@pytest.mark.anyio
async def test_the_admin_role_does_not_edit_or_reclassify(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await _registered(dbsession, ContainerClass.SYSTEM_AUX)
    app = _as(fastapi_app, await _user(dbsession, superuser=False, role="admin"), docker, root_mode=False)
    r = await client.put(app.url_path_for("update_container", container_id=OLD_ID), json=_create())
    assert r.status_code == status.HTTP_403_FORBIDDEN
    r = await client.put(
        app.url_path_for("register_container", container_id=OLD_ID),
        json={"container_class": "TENANT_APP", "owner_scope": "platform", "policy": "free"},
    )
    assert r.status_code == status.HTTP_403_FORBIDDEN
    entry = await ContainerRegistryDAO(dbsession).get(OLD_ID)
    assert entry is not None and entry.container_class == ContainerClass.SYSTEM_AUX
    docker.create_from_config.assert_not_called()


@pytest.mark.anyio
async def test_each_lifecycle_action_needs_its_own_permission(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """The operator role has start, stop and restart; pausing needs ``containers:pause``.

    Root mode is overridden: the per-container routes also ask for a
    superuser through it, which is what the permission is checked under here.
    """
    await _registered(dbsession)
    app = _as(fastapi_app, await _user(dbsession, superuser=False, role="operator"), docker, root_mode=False)

    def url(action: str) -> str:
        return app.url_path_for("container_lifecycle", container_id=OLD_ID, action=action)

    for action in ("start", "stop", "restart"):
        assert (await client.post(url(action))).status_code == status.HTTP_204_NO_CONTENT, action
    for action in ("pause", "unpause"):
        r = await client.post(url(action))
        assert r.status_code == status.HTTP_403_FORBIDDEN, action
        assert r.json()["detail"] == "Permission denied: containers:pause"
    docker.pause.assert_not_called()
    docker.unpause.assert_not_called()
    assert (await client.post(url("explode"))).status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_the_viewer_role_starts_nothing(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await _registered(dbsession)
    app = _as(fastapi_app, await _user(dbsession, superuser=False, role="viewer"), docker, root_mode=False)
    r = await client.post(app.url_path_for("container_lifecycle", container_id=OLD_ID, action="start"))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    docker.start.assert_not_called()
