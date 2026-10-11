"""What every part of this install runs, and what this release expects it to run.

Read from what is already here, so it works without internet: the backend's own
package; the core containers' images through the Docker socket (the release's OCI
version label, else the image tag); the agents' versions from their heartbeats,
against :data:`AGENT_VERSION`; and the runtime each cluster runs, against its
flavor's default.
"""

from __future__ import annotations

import logging
import socket
import tomllib
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.updates import release_feed

log = logging.getLogger(__name__)

#: The compose services built from this repository, which run the install's release.
CORE_SERVICES = (
    "faden-backend",
    "faden-backend-worker",
    "faden-gateway",
    "faden-frontend",
    "faden-pii",
    "faden-pii-worker",
    "faden-mcp",
    "faden-skills",
)
VERSION_LABEL = "org.opencontainers.image.version"
REVISION_LABEL = "org.opencontainers.image.revision"


def backend_version() -> str:
    """This backend's package version (from the checkout's pyproject.toml when it is not installed)."""
    try:
        return version("faden-backend")
    except PackageNotFoundError:
        pass
    try:
        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(project["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


@dataclass
class Part:
    """One part of the install and the release it runs."""

    kind: str  # service | agent | cluster | cli
    name: str
    #: What it runs; None when it cannot be told (a local build, a machine that never reported).
    running: str | None
    #: What this install's release expects it to run.
    expected: str | None
    #: current | update (a newer one is here to take) | mismatch (another release) | unknown | stopped
    status: str
    detail: str | None = None
    link: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return asdict(self)


def _tag_version(image: str) -> str | None:
    """The version in an image reference's tag (``ghcr.io/fadenstack/gateway:0.4.1``)."""
    name = image.rsplit("@", 1)[0]
    if ":" not in name.rsplit("/", 1)[-1]:
        return None
    tag = name.rsplit(":", 1)[1]
    return tag if release_feed.parse_version(tag) is not None else None


async def _own_project(docker: Any) -> str | None:
    """The compose project this backend's container belongs to; None when it runs outside one."""
    try:
        own = await docker.inspect_container(socket.gethostname())
    except Exception:  # not in a container (a source checkout)
        return None
    return ((own.get("Config") or {}).get("Labels") or {}).get("com.docker.compose.project")


async def services(docker: Any, expected: str) -> list[Part]:
    """The core containers and the release each runs. Raises when Docker cannot be asked."""
    containers = await docker.list_containers(all_=True)
    project = await _own_project(docker)
    labels_by_image: dict[str, dict[str, str]] = {}
    parts: list[Part] = []
    for c in containers:
        labels = c.get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service not in CORE_SERVICES:
            continue
        if project and labels.get("com.docker.compose.project") != project:
            continue
        image_id = str(c.get("ImageID") or "")
        if image_id not in labels_by_image:
            try:
                inspected = await docker.inspect_image(image_id)
                labels_by_image[image_id] = (inspected.get("Config") or {}).get("Labels") or {}
            except Exception:  # an image removed under its container
                labels_by_image[image_id] = {}
        image_labels = labels_by_image[image_id]
        label = image_labels.get(VERSION_LABEL)
        running = label if release_feed.parse_version(label) else _tag_version(str(c.get("Image") or ""))
        if c.get("State") != "running":
            status = "stopped"
        elif running is None:
            status = "unknown"
        elif release_feed.parse_version(running) == release_feed.parse_version(expected):
            status = "current"
        else:
            status = "mismatch"
        revision = image_labels.get(REVISION_LABEL)
        parts.append(Part(
            "service",
            service,
            running,
            expected,
            status,
            detail=f"{c.get('Image')}{f' ({revision[:12]})' if revision else ''}",
            link="/admin/containers",
        ))
    parts.sort(key=lambda p: CORE_SERVICES.index(p.name))
    return parts


async def agents(session: AsyncSession) -> list[Part]:
    """Every machine's agent against the version this backend hands out."""
    from faden_backend.db.models.node_control import InfraNode  # noqa: PLC0415
    from faden_backend.web.api.install import AGENT_VERSION, newer_agent_version  # noqa: PLC0415

    parts = []
    for node in (await session.execute(select(InfraNode).order_by(InfraNode.host))).scalars():
        if not node.version:
            status = "unknown"
        elif newer_agent_version(node.version):
            status = "update"
        elif node.version == AGENT_VERSION:
            status = "current"
        else:
            status = "mismatch"  # newer than this backend's: a machine updated by hand
        parts.append(
            Part("agent", node.host, node.version, AGENT_VERSION, status, link=f"/admin/nodes/{node.id}")
        )
    return parts


async def clusters(session: AsyncSession) -> list[Part]:
    """Each cluster's runtime, and whether this server ships a newer stable one for it."""
    from faden_backend.db.models.inference import (  # noqa: PLC0415
        EnvironmentDesiredState,
        InferenceEnvironment,
    )
    from faden_backend.services.inference.runtime_choice import environment_status  # noqa: PLC0415

    envs = (
        await session.execute(
            select(InferenceEnvironment)
            .where(InferenceEnvironment.desired_state != EnvironmentDesiredState.DELETED.value)
            .order_by(InferenceEnvironment.name)
        )
    ).scalars().all()
    parts = []
    for env in envs:
        try:
            found = await environment_status(session, env.id)
        except Exception as exc:  # one cluster's trouble is not the page's
            log.warning("Runtime status of cluster %s: %s", env.name, exc)
            continue
        running = ", ".join(found["runs"]) or None
        link = f"/admin/clusters/{env.id}"
        if found["update"]:
            parts.append(Part("cluster", env.name, running, found["update"], "update", link=link))
        else:
            state = "current" if running else "unknown"
            parts.append(Part("cluster", env.name, running, running, state, link=link))
    return parts


async def outdated_agents(session: AsyncSession) -> int:
    """How many machines run an older agent than this backend hands out."""
    return sum(1 for p in await agents(session) if p.status == "update")
