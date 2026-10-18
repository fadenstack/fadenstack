"""Fadenstack's own volumes: a container that mounts one is made, changed or classified only in Root Mode.

A named volume is not the host's (``host_access.py``), but Fadenstack's own hold what the console keeps from
administrators: the database's files, the chat files, the audit trail and its copy, the models. A container
that mounts one reads them past every check the console makes. A volume is Fadenstack's own when compose
made it for the project the backend runs in, or when one of that project's containers uses it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aiodocker.exceptions import DockerError

from faden_backend.services.docker import recreate
from faden_backend.services.docker.client import DockerService

#: The label compose puts on the containers and volumes it makes.
COMPOSE_PROJECT = "com.docker.compose.project"
#: The project of shared/docker-compose.yaml (``name: fadenstack``), for a backend outside a container.
DEFAULT_PROJECT = "fadenstack"


async def own_project(docker: DockerService) -> str:
    """The compose project the backend runs in."""
    own = recreate.own_container_id()
    if own:
        try:
            labels = ((await docker.inspect_container(own)).get("Config") or {}).get("Labels") or {}
        except DockerError:
            labels = {}
        if project := labels.get(COMPOSE_PROJECT):
            return str(project)
    return DEFAULT_PROJECT


async def _project_of(docker: DockerService, name: str) -> str | None:
    """The compose project that made the volume *name*; None for one it did not make, or one not there yet."""
    try:
        volume = await docker.inspect_volume(name)
    except DockerError:
        return None  # Docker makes it, empty
    return (volume.get("Labels") or {}).get(COMPOSE_PROJECT)


def _used_by(containers: Iterable[dict[str, Any]], project: str) -> set[str]:
    """The named volumes the containers of *project* mount (``docker ps -a``'s summaries)."""
    used: set[str] = set()
    for container in containers:
        if (container.get("Labels") or {}).get(COMPOSE_PROJECT) != project:
            continue
        for mount in container.get("Mounts") or []:
            if str(mount.get("Type") or "").lower() == "volume" and mount.get("Name"):
                used.add(str(mount["Name"]))
    return used


async def find(docker: DockerService, names: Iterable[str]) -> list[str]:
    """Those of the named volumes *names* that are Fadenstack's own."""
    names = list(dict.fromkeys(names))
    if not names:
        return []
    project = await own_project(docker)
    try:
        used = _used_by(await docker.list_containers(all_=True), project)
    except DockerError:
        used = set()
    return [name for name in names if name in used or await _project_of(docker, name) == project]


def refusal(volumes: list[str], *, doing: str) -> str:
    """What the console says when a container mounts Fadenstack's own volumes outside Root Mode."""
    return (
        f"Fadenstack's own data: this container mounts {', '.join(volumes)}, which only Fadenstack's own "
        f"services may use. Activate Root Mode to {doing} a container like this."
    )
