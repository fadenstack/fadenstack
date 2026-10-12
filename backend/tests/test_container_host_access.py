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
        # No list of the host's dangerous folders is complete: every one of
        # these is root on the host, and none is a system folder.
        "/var/spool/cron:/cron",
        "/home/admin/.ssh:/keys:ro",
        "/home/admin/fadenstack:/install",  # the install directory: .env, the compose files
        "/srv/docker-data:/data",  # a Docker data root moved off /var/lib/docker
        "/var/lib/containerd:/c",
        "/opt:/opt",
        "/srv/data:/data",
        "./data:/data",  # relative: a folder of the backend's working directory
        "../shared:/shared",
        "~/data:/data",
        "~:/home",
        ".:/here",
        "C:/models:/models",  # Docker Desktop: a folder of drive C
        "C:\\Users\\admin:/home",
        "c:data",
        "type=bind,source=/srv,target=/srv",  # --mount style: not a bind Docker takes, refused all the same
    ],
)
def test_every_host_path_needs_root_mode(bind: str) -> None:
    (reason,) = host_access.bind_reasons([bind])
    assert reason.startswith("binds "), reason


@pytest.mark.parametrize(
    ("bind", "reason"),
    [
        ("/srv/data:/data", "binds /srv/data (a host folder)"),
        ("/var/run/docker.sock:/var/run/docker.sock", "binds /var/run/docker.sock (the container engine's socket)"),
        ("/:/host", "binds / (the host's root)"),
        ("/etc/cron.d:/cron", "binds /etc/cron.d (the host's /etc)"),
        ("type=bind,source=/var/run/docker.sock,target=/s", "binds /var/run/docker.sock (the container engine's socket)"),
    ],
)
def test_the_refusal_names_the_plainest_reason(bind: str, reason: str) -> None:
    assert host_access.bind_reasons([bind]) == [reason]


@pytest.mark.parametrize(
    "bind",
    [
        "fadenstack_loki_data:/loki:rw",  # a named volume
        "faden.models-2:/models:ro",
        "/cache",  # a container path alone: an anonymous volume
        "/etc/localtime:/etc/localtime:ro",
        "/etc/timezone:/etc/timezone:ro",
        "/usr/share/zoneinfo:/usr/share/zoneinfo:ro",
        "/usr/share/zoneinfo/Europe/Berlin:/etc/localtime:ro",
    ],
)
def test_a_volume_or_the_time_zone_read_only_passes(bind: str) -> None:
    assert host_access.bind_reasons([bind]) == []


@pytest.mark.parametrize(
    "bind",
    ["/etc/localtime:/etc/localtime", "/usr/share/zoneinfo:/usr/share/zoneinfo:rw", "/etc/localtime:/etc/localtime:z"],
)
def test_the_time_zone_is_harmless_only_read_only(bind: str) -> None:
    assert host_access.bind_reasons([bind])


def test_a_refused_host_folder_points_to_named_volumes() -> None:
    reasons = host_access.bind_reasons(["/srv/data:/data"])
    assert "named volume" in host_access.refusal(reasons, doing="create")
    assert "named volume" not in host_access.refusal(["runs privileged"], doing="create")


@pytest.mark.parametrize(("network", "found"), [
    ("host", True), ("container:abc", True), ("fadenstack_default", False), ("bridge", False), ("", False), (None, False),
])
def test_the_network_modes_that_share_the_host_are_found(network: str | None, found: bool) -> None:
    assert bool(host_access.network_reason(network)) is found


@pytest.mark.parametrize("kept", [
    {"Privileged": True},
    {"CapAdd": ["SYS_ADMIN"]},
    {"PidMode": "host"},
    {"PidMode": "container:faden-backend"},  # its processes, and through /proc their files: the socket
    {"IpcMode": "host"},
    {"UsernsMode": "host"},
    {"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda", "CgroupPermissions": "rwm"}]},
    {"DeviceCgroupRules": ["b *:* rwm"]},
    {"VolumesFrom": ["faden-backend-worker"]},  # its binds: the Docker socket
    {"SecurityOpt": ["systempaths=unconfined"]},
    {"Mounts": [{"Type": "bind", "Source": "/var/run/docker.sock"}]},
    {"Mounts": [{"Type": "bind", "Source": "/srv/data", "Target": "/data"}]},
    {"Mounts": [{"Type": "volume", "Source": "rootfs", "Target": "/host", "VolumeOptions": {
        "DriverConfig": {"Name": "local", "Options": {"type": "none", "o": "bind", "device": "/"}},
    }}]},
])
def test_what_an_edit_keeps_from_the_old_container_counts_too(kept: dict[str, Any]) -> None:
    assert host_access.kept_reasons(kept)


def test_a_gpu_a_restart_policy_and_a_volume_are_kept_as_they_were() -> None:
    assert host_access.kept_reasons({
        "DeviceRequests": [{"Driver": "nvidia", "Count": -1}], "RestartPolicy": {"Name": "unless-stopped"},
        "Devices": [{"PathOnHost": "/dev/kfd"}, {"PathOnHost": "/dev/dri/renderD128"}],  # ROCm
        "IpcMode": "shareable", "SecurityOpt": ["seccomp=unconfined"],
        "Mounts": [{"Type": "volume", "Source": "faden_models"}, {"Type": "tmpfs", "Target": "/scratch"}],
    }) == []


def test_a_container_is_judged_by_what_it_runs_with() -> None:
    """Its Binds, its Mounts and the rest of its HostConfig: what register and exec look at."""
    socket = {
        "HostConfig": {"Binds": [SOCKET], "NetworkMode": "fadenstack_default"},
        "Mounts": [{"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": False}],
    }
    assert host_access.container_reasons(socket) == ["binds /var/run/docker.sock (the container engine's socket)"]
    mounted = {"HostConfig": {}, "Mounts": [{"Type": "bind", "Source": "/home/admin", "RW": True}]}
    assert host_access.container_reasons(mounted) == ["binds /home/admin (a host folder)"]
    assert host_access.container_reasons({"HostConfig": {"NetworkMode": "host"}}) == ["uses the host's network"]
    plain = {
        "HostConfig": {"Binds": ["tool_data:/data"], "NetworkMode": "fadenstack_default"},
        "Mounts": [{"Type": "volume", "Name": "tool_data", "Source": "/var/lib/docker/volumes/tool_data/_data"}],
    }
    assert host_access.container_reasons(plain) == []


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
        "HostConfig": {"NetworkMode": "fadenstack_default", "Binds": ["tool_data:/data"], **host},
        "NetworkSettings": {"Networks": {"fadenstack_default": {"Aliases": ["tool"]}}},
    }


def _socket_raw(name: str = "faden-backend-worker") -> dict[str, Any]:
    """One of Fadenstack's own containers that binds the Docker socket, as Docker shows it."""
    raw = _raw(Binds=[SOCKET, "faden_models:/srv/faden/models"])
    raw["Name"] = f"/{name}"
    raw["Mounts"] = [
        {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": False},
    ]
    return raw


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
        "volumes": ["tool_data:/data"], "network": "fadenstack_default", **changes,
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
    """Named volumes and a network of its own: no Root Mode."""
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    volumes = ["tool_data:/data", "tool_cache:/cache:ro", "/scratch", "/etc/localtime:/etc/localtime:ro"]
    r = await client.post(app.url_path_for("create_container"), json=_create(volumes=volumes))
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert docker.create_container.call_args.kwargs["volumes"] == volumes


@pytest.mark.anyio
async def test_an_image_the_server_lacks_is_named_not_a_500(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """Docker creates only from images it holds; the UI sweep on .61 met a bare 500 for node:22-alpine."""
    from aiodocker.exceptions import DockerError

    docker.create_container = AsyncMock(side_effect=DockerError(404, {"message": "No such image: node:22-alpine"}))
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    r = await client.post(app.url_path_for("create_container"), json=_create())
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "node:22-alpine is not on this server" in r.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [
    {"volumes": [SOCKET]}, {"volumes": ["/:/host"]}, {"network": "host"},
    {"volumes": ["/srv/data:/data"]}, {"volumes": ["tool_data:/data", "./config:/config:ro"]},
    {"volumes": ["/var/spool/cron:/cron"]}, {"volumes": ["C:/Users/admin/.ssh:/keys:ro"]},
])
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


# ── Register and exec: what the container already has ─────────────

_TENANT = {"container_class": "TENANT_APP", "owner_scope": "platform", "policy": "free"}


@pytest.mark.anyio
async def test_a_container_with_the_socket_is_reclassified_only_in_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """Made TENANT_APP, a container with the Docker socket could be exec'd into: root on the host."""
    docker.inspect_container = AsyncMock(return_value=_socket_raw("portainer"))  # UNTRUSTED by its name
    user = await _user(dbsession, superuser=True)
    url = fastapi_app.url_path_for("register_container", container_id=OLD_ID)
    _as(fastapi_app, user, docker)
    r = await client.put(url, json=_TENANT)
    assert r.status_code == status.HTTP_403_FORBIDDEN
    detail = r.json()["detail"]
    assert "/var/run/docker.sock" in detail and "Activate Root Mode to reclassify" in detail
    assert await ContainerRegistryDAO(dbsession).get(OLD_ID) is None

    _as(fastapi_app, user, docker, root_mode=True)
    r = await client.put(url, json=_TENANT)
    assert r.status_code == status.HTTP_200_OK, r.text
    entry = await ContainerRegistryDAO(dbsession).get(OLD_ID)
    assert entry is not None and entry.container_class == ContainerClass.TENANT_APP


@pytest.mark.anyio
async def test_a_system_container_keeps_its_class_without_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """Unregistered, it has the class its name gives it; named in the path, it is the same container."""
    docker.inspect_container = AsyncMock(return_value={**_raw(), "Name": "/faden-gateway"})
    user = await _user(dbsession, superuser=True)
    _as(fastapi_app, user, docker)
    registry = ContainerRegistryDAO(dbsession)
    for path in (OLD_ID, "faden-gateway", OLD_ID[:12]):
        r = await client.put(fastapi_app.url_path_for("register_container", container_id=path), json=_TENANT)
        assert r.status_code == status.HTTP_403_FORBIDDEN, path
        assert "SYSTEM_AUX" in r.json()["detail"]
        assert await registry.get(path) is None
    # Registered as what it is (the API docs page does), it needs nothing.
    aux = {**_TENANT, "container_class": "SYSTEM_AUX"}
    r = await client.put(fastapi_app.url_path_for("register_container", container_id="faden-gateway"), json=aux)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert await registry.get("faden-gateway") is None  # kept under its full id, which the list uses
    entry = await registry.get(OLD_ID)
    assert entry is not None and entry.container_class == ContainerClass.SYSTEM_AUX

    _as(fastapi_app, user, docker, root_mode=True)
    r = await client.put(fastapi_app.url_path_for("register_container", container_id="faden-gateway"), json=_TENANT)
    assert r.status_code == status.HTTP_200_OK, r.text
    dbsession.expire_all()  # the upsert is plain SQL: the entry read above is stale
    entry = await registry.get(OLD_ID)
    assert entry is not None and entry.container_class == ContainerClass.TENANT_APP


@pytest.mark.anyio
async def test_a_locked_system_container_is_unlocked_only_in_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="tool", container_class=ContainerClass.SYSTEM_CORE, policy=ContainerPolicy.LOCKED,
    )
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    r = await client.put(
        app.url_path_for("register_container", container_id=OLD_ID),
        json={"container_class": "SYSTEM_CORE", "owner_scope": "platform", "policy": "free"},
    )
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "policy from locked" in r.json()["detail"]


@pytest.mark.anyio
async def test_an_edit_gives_a_system_container_another_class_only_in_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """The new container takes the form's class: an edit is a register too."""
    await _registered(dbsession, ContainerClass.SYSTEM_AUX)
    user = await _user(dbsession, superuser=True)
    url = fastapi_app.url_path_for("update_container", container_id=OLD_ID)
    _as(fastapi_app, user, docker)
    r = await client.put(url, json=_create())  # TENANT_APP
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "SYSTEM_AUX" in r.json()["detail"]
    docker.create_from_config.assert_not_called()
    r = await client.put(url, json=_create(container_class="SYSTEM_AUX"))
    assert r.status_code == status.HTTP_200_OK, r.text


@pytest.mark.anyio
async def test_an_edit_that_binds_a_host_folder_needs_root_mode(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await _registered(dbsession)
    user = await _user(dbsession, superuser=True)
    url = fastapi_app.url_path_for("update_container", container_id=OLD_ID)
    _as(fastapi_app, user, docker)
    r = await client.put(url, json=_create(volumes=["/home/admin/.ssh:/keys:ro"]))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "binds /home/admin/.ssh (a host folder)" in r.json()["detail"]
    docker.create_from_config.assert_not_called()
    _as(fastapi_app, user, docker, root_mode=True)
    r = await client.put(url, json=_create(volumes=["/home/admin/.ssh:/keys:ro"]))
    assert r.status_code == status.HTTP_200_OK, r.text


@pytest.mark.anyio
async def test_an_operator_still_starts_and_stops_a_container_with_the_socket(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    """Start, stop and restart run nothing new in it: no Root Mode (overridden as above)."""
    await _registered(dbsession)
    docker.inspect_container = AsyncMock(return_value=_socket_raw("tool"))
    app = _as(fastapi_app, await _user(dbsession, superuser=False, role="operator"), docker, root_mode=False)
    for action in ("start", "stop", "restart"):
        r = await client.post(app.url_path_for("container_lifecycle", container_id=OLD_ID, action=action))
        assert r.status_code == status.HTTP_204_NO_CONTENT, action


@pytest.mark.anyio
@pytest.mark.parametrize("address", ["tool", OLD_ID[:12]], ids=["by-name", "by-short-id"])
async def test_a_locked_container_stays_locked_when_addressed_by_name_or_short_id(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock, address: str,
) -> None:
    """The registry is keyed by the full id; a name in the path used to fall back to the unregistered default.

    In Root Mode that default let everything through, which a lock must not.
    """
    await ContainerRegistryDAO(dbsession).upsert(
        container_id=OLD_ID, name="tool", container_class=ContainerClass.TENANT_APP, policy=ContainerPolicy.LOCKED,
    )
    docker.inspect_container = AsyncMock(return_value=_raw())  # whatever it is called, it is this container
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker, root_mode=True)

    # By the full id: refused (the baseline).
    r = await client.post(app.url_path_for("container_lifecycle", container_id=OLD_ID, action="stop"))
    assert r.status_code == status.HTTP_403_FORBIDDEN

    r = await client.post(app.url_path_for("container_lifecycle", container_id=address, action="stop"))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    r = await client.put(app.url_path_for("update_container", container_id=address), json=_create())
    assert r.status_code == status.HTTP_403_FORBIDDEN
    r = await client.delete(app.url_path_for("delete_container", container_id=address))
    assert r.status_code == status.HTTP_403_FORBIDDEN
    docker.stop.assert_not_called()
    docker.delete.assert_not_called()
    docker.create_from_config.assert_not_called()


@pytest.mark.anyio
async def test_a_container_addressed_by_name_gets_the_class_it_is_registered_with(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, docker: MagicMock,
) -> None:
    await _registered(dbsession)  # TENANT_APP, free: may be stopped
    docker.inspect_container = AsyncMock(return_value=_raw())
    app = _as(fastapi_app, await _user(dbsession, superuser=True), docker)
    r = await client.post(app.url_path_for("container_lifecycle", container_id="tool", action="stop"))
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
