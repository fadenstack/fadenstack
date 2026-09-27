"""Edit a container: replace it with one made from its settings and the edits.

Docker changes none of what the console edits -- image, command, environment,
ports, volumes, network -- in a container that exists, so an edit is a new
container. It takes what the old one was created with and the edits:

- what the old one's image supplied (its environment, command, entrypoint,
  labels, working directory, ...) is left out, so a new image brings its own;
- everything else the old one was created with -- GPU devices, restart
  policy, labels such as Compose's, health check, entrypoint -- is kept;
- it joins the same networks under the same aliases (Compose's service
  names: other containers reach Postgres as "postgres").

The old container is renamed aside, not removed, until the new one runs: a
new container that cannot be created or started is removed and the old one
is back as it was, under its name, started again if it was running.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_port_backend.services.docker.client import DockerService, resolve_binds

logger = logging.getLogger(__name__)

#: Config fields an image supplies: kept only where the container set its own.
_IMAGE_FIELDS = (
    "Cmd", "Entrypoint", "WorkingDir", "User", "Healthcheck", "StopSignal",
    "Volumes", "Shell", "OnBuild", "ArgsEscaped",
)
#: Config fields of the container alone: kept as they are.
_CONTAINER_FIELDS = (
    "Domainname", "Tty", "OpenStdin", "StdinOnce", "AttachStdin", "AttachStdout",
    "AttachStderr", "StopTimeout", "NetworkDisabled", "MacAddress",
)
#: Network modes that are not a network to join.
_NOT_A_NETWORK = ("default", "bridge", "host", "none")


@dataclass(slots=True)
class ContainerEdit:
    """What the console edits: the fields of its create form."""

    image: str
    name: str
    cmd: list[str] | None = None
    env: list[str] = field(default_factory=list)
    #: ``{"8080/tcp": [{"HostIp": "", "HostPort": "8088"}]}``
    ports: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    network: str = ""
    start: bool = True


def _env_dict(env: list[str] | None) -> dict[str, str]:
    return dict(item.split("=", 1) if "=" in item else (item, "") for item in env or [])


def own_settings(raw: dict[str, Any], image: dict[str, Any]) -> ContainerEdit:
    """The container as its create form would show it: what it set, not what its image did."""
    config = raw.get("Config") or {}
    host = raw.get("HostConfig") or {}
    image_config = image.get("Config") or {}
    image_env = _env_dict(image_config.get("Env"))
    cmd = config.get("Cmd")
    network = host.get("NetworkMode") or ""
    return ContainerEdit(
        image=config.get("Image", ""),
        name=(raw.get("Name") or "").lstrip("/"),
        cmd=cmd if cmd and cmd != image_config.get("Cmd") else None,
        env=[
            f"{key}={value}" for key, value in _env_dict(config.get("Env")).items()
            if image_env.get(key) != value
        ],
        ports={port: list(binds or []) for port, binds in (host.get("PortBindings") or {}).items()},
        volumes=list(host.get("Binds") or []),
        network="" if network == "default" else network,
        start=bool((raw.get("State") or {}).get("Running")),
    )


def _aliases(raw: dict[str, Any], network: str) -> list[str]:
    """The names *raw* answers to on *network*, but its own id: the new one has another."""
    endpoint = ((raw.get("NetworkSettings") or {}).get("Networks") or {}).get(network) or {}
    short_id = (raw.get("Id") or "")[:12]
    return [alias for alias in endpoint.get("Aliases") or [] if alias != short_id]


def _endpoint(raw: dict[str, Any], network: str) -> dict[str, Any]:
    endpoint: dict[str, Any] = {}
    if aliases := _aliases(raw, network):
        endpoint["Aliases"] = aliases
    old = ((raw.get("NetworkSettings") or {}).get("Networks") or {}).get(network) or {}
    if old.get("IPAMConfig"):
        endpoint["IPAMConfig"] = old["IPAMConfig"]
    return endpoint


def _kept(raw: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    """What the old container was created with, beyond what its image supplied and the form edits."""
    old = raw.get("Config") or {}
    image_config = image.get("Config") or {}
    kept: dict[str, Any] = {
        key: old[key] for key in _IMAGE_FIELDS
        if key != "Cmd" and old.get(key) not in (None, "", [], {}) and old.get(key) != image_config.get(key)
    }
    kept.update({key: old[key] for key in _CONTAINER_FIELDS if old.get(key) not in (None, "", [], {})})
    hostname = old.get("Hostname")
    if hostname and hostname != (raw.get("Id") or "")[:12]:
        kept["Hostname"] = hostname
    image_labels = image_config.get("Labels") or {}
    labels = {k: v for k, v in (old.get("Labels") or {}).items() if image_labels.get(k) != v}
    if labels:
        kept["Labels"] = labels
    return kept


def replacement(
    raw: dict[str, Any], image: dict[str, Any], edit: ContainerEdit,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """The create body for *raw* edited by *edit*, and the other networks to join after it.

    *image* is the old container's image: what it supplied is left for the
    new image to supply.
    """
    config: dict[str, Any] = {"Image": edit.image, **_kept(raw, image)}
    if edit.cmd:
        config["Cmd"] = edit.cmd
    if edit.env:
        config["Env"] = edit.env
    if edit.ports:
        config["ExposedPorts"] = {port: {} for port in edit.ports}

    host = dict(raw.get("HostConfig") or {})
    host["PortBindings"] = edit.ports or None
    host["Binds"] = resolve_binds(edit.volumes) or None
    old_networks = list((raw.get("NetworkSettings") or {}).get("Networks") or {})
    network = edit.network or "default"
    host["NetworkMode"] = network
    config["HostConfig"] = host

    others: list[tuple[str, dict[str, Any]]] = []
    if network not in _NOT_A_NETWORK and not network.startswith("container:"):
        config["NetworkingConfig"] = {"EndpointsConfig": {network: _endpoint(raw, network)}}
        # The networks it joined besides its own, as it joined them. Not the
        # built-in ones: a container is on "bridge" by its network mode only.
        others = [(n, _endpoint(raw, n)) for n in old_networks if n != network and n not in _NOT_A_NETWORK]
    return config, others


def aside_name(raw: dict[str, Any]) -> str:
    """The name the old container waits under while the new one takes its own."""
    name = (raw.get("Name") or "").lstrip("/")
    return f"{name}-replaced-{(raw.get('Id') or '')[:8]}"


async def replace(
    docker: DockerService, raw: dict[str, Any], config: dict[str, Any],
    others: list[tuple[str, dict[str, Any]]], *, name: str, start: bool,
) -> dict[str, Any]:
    """Replace the container *raw* by one made from *config*; the new one's inspect.

    Nothing is lost on failure: the new container is removed, and the old one
    takes its name back and runs again if it ran.
    """
    old_id = raw["Id"]
    old_name = (raw.get("Name") or "").lstrip("/")
    was_running = bool((raw.get("State") or {}).get("Running"))

    await docker.rename(old_id, aside_name(raw))
    try:
        new_id = await docker.create_from_config(config, name=name)
    except Exception:
        await docker.rename(old_id, old_name)
        raise
    try:
        for network, endpoint in others:
            await docker.connect_with_endpoint(network, new_id, endpoint)
        if was_running:
            await docker.stop(old_id)
        if start:
            await docker.start(new_id)
    except Exception:
        await _put_back(docker, old_id, old_name, new_id, restart=was_running)
        raise
    try:
        await docker.delete(old_id)
    except Exception:  # the new one runs; the old one is left stopped, aside
        logger.warning("Replaced container %s, but could not remove it", old_id[:12], exc_info=True)
    return await docker.inspect_container(new_id)


async def _put_back(docker: DockerService, old_id: str, old_name: str, new_id: str, *, restart: bool) -> None:
    """Undo a replacement that failed half way."""
    for step in (
        lambda: docker.delete(new_id, force=True),
        lambda: docker.rename(old_id, old_name),
        (lambda: docker.start(old_id)) if restart else None,
    ):
        if step is None:
            continue
        try:
            await step()
        except Exception:
            logger.exception("Could not put container %s back as it was", old_id[:12])


# ── The container the backend runs in ─────────────────────────────

_OWN_ID = re.compile(r"/containers/([0-9a-f]{64})/")


def own_container_id(mountinfo: Path = Path("/proc/self/mountinfo")) -> str | None:
    """The id of the Docker container this process runs in, if it runs in one.

    Docker mounts the container's hostname, hosts and resolv.conf from
    ``.../containers/<id>/``.
    """
    try:
        match = _OWN_ID.search(mountinfo.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return match.group(1) if match else None
