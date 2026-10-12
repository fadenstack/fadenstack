"""Admin container lifecycle and management endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any, NoReturn

from aiodocker.exceptions import DockerError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.containers import (
    AuditResult,
    ContainerClass,
    ContainerPolicy,
)
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.docker import recreate
from faden_backend.services.docker.client import DockerService
from faden_backend.services.policy.enforcement import Action, PolicyEnforcer
from faden_backend.web.api.admin.containers import host_access
from faden_backend.web.api.admin.containers.schema import (
    ContainerDetailDTO,
    ContainerSettingsDTO,
    ContainerSummaryDTO,
    CreateContainerRequest,
    PortBinding,
    RegisterContainerRequest,
)
from faden_backend.web.api.admin.dependencies import (
    audit_action,
    get_docker,
    get_policy_enforcer,
    get_registry_entry,
    get_root_mode_active,
    require_superuser,
)
from faden_backend.web.api.rbac import require_permission

router = APIRouter()
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_LIFECYCLE_ACTION_MAP: dict[str, Action] = {
    "start": Action.START,
    "stop": Action.STOP,
    "restart": Action.RESTART,
    "pause": Action.PAUSE,
    "unpause": Action.UNPAUSE,
}
#: The ``containers`` permission each lifecycle action needs. There is no
#: ``unpause`` permission: who may pause a container may resume it.
_LIFECYCLE_PERMISSION: dict[str, str] = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "pause": "pause",
    "unpause": "pause",
}

_ROOT_MODE_SEVERITY = "high"
_NORMAL_SEVERITY = "normal"


def _severity(root_mode: bool) -> str:
    return _ROOT_MODE_SEVERITY if root_mode else _NORMAL_SEVERITY


async def _lifecycle_user(
    action: str,
    user: User = Depends(current_active_user),
    rbac_dao: RbacDAO = Depends(),
) -> User:
    """The user, holding the ``containers`` permission of *action*: stop needs ``stop``, not ``start``."""
    permission = _LIFECYCLE_PERMISSION.get(action)
    if permission is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown action '{action}'. Valid: {list(_LIFECYCLE_ACTION_MAP)}",
        )
    return await require_permission("containers", permission)(user=user, rbac_dao=rbac_dao)


async def _deny(
    detail: str, metadata: dict[str, Any], *, audit: str, target_id: str, user: User, audit_dao: AuditDAO,
) -> NoReturn:
    """Refuse what needs Root Mode, audited at high severity."""
    await audit_action(
        action=audit,
        target_type="container",
        target_id=target_id,
        result=AuditResult.DENY,
        actor_id=user.id,
        severity=_ROOT_MODE_SEVERITY,
        audit_dao=audit_dao,
        metadata_json=json.dumps(metadata),
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _refuse_host_access(
    reasons: list[str],
    *,
    root_mode: bool,
    doing: str,
    audit: str,
    target_id: str,
    user: User,
    audit_dao: AuditDAO,
) -> None:
    """Refuse a container with root on the host unless Root Mode is on; the refusal is audited."""
    if not reasons or root_mode:
        return
    await _deny(
        host_access.refusal(reasons, doing=doing), {"host_access": reasons},
        audit=audit, target_id=target_id, user=user, audit_dao=audit_dao,
    )


#: Fadenstack's own containers: the console changes their class only in Root Mode.
_SYSTEM_CLASSES = frozenset({ContainerClass.SYSTEM_CORE, ContainerClass.SYSTEM_AUX})


async def _classification(
    raw: dict[str, Any], container_id: str, registry_dao: ContainerRegistryDAO,
) -> tuple[ContainerClass, ContainerPolicy]:
    """The class and policy a container has now: its registry entry, or the class the list gives it by name.

    The registry is keyed by the full id the list uses; a container named in
    the path by its name or a short id has no entry of its own.
    """
    for key in dict.fromkeys((raw.get("Id") or container_id, container_id)):
        if (entry := await registry_dao.get(key)) is not None:
            return entry.container_class, entry.policy
    return _classify_by_name((raw.get("Name") or "").lstrip("/"))[0], ContainerPolicy.FREE


async def _refuse_system_change(
    current: tuple[ContainerClass, ContainerPolicy],
    container_class: ContainerClass,
    policy: ContainerPolicy,
    *,
    root_mode: bool,
    audit: str,
    target_id: str,
    user: User,
    audit_dao: AuditDAO,
) -> None:
    """Refuse a new class for a system container, or unlocking one, unless Root Mode is on.

    A system container made ``TENANT_APP`` may be exec'd into and deleted.
    """
    cls, old_policy = current
    if root_mode or cls not in _SYSTEM_CLASSES:
        return
    if container_class != cls:
        change = f"its class from {cls.value} to {container_class.value}"
    elif old_policy == ContainerPolicy.LOCKED and policy != ContainerPolicy.LOCKED:
        change = f"its policy from locked to {policy.value}"
    else:
        return
    await _deny(
        f"This is a {cls.value} container, one of Fadenstack's own: activate Root Mode to change {change}.",
        {"class": [cls.value, container_class.value], "policy": [old_policy.value, policy.value]},
        audit=audit, target_id=target_id, user=user, audit_dao=audit_dao,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Auto-classification rules by container name / compose project
# ──────────────────────────────────────────────────────────────────────────────

# Containers whose name contains any of these substrings are classified accordingly.
# Rules are evaluated top-to-bottom; first match wins.
_AUTO_CLASS_RULES: list[tuple[list[str], ContainerClass, str]] = [
    # SYSTEM_CORE — shared platform infrastructure (non-editable)
    (
        [
            "postgres", "clickhouse", "redis", "nginx",  # data & proxy
            "rabbitmq", "rmq",                            # message broker
            "minio",                                      # object storage
            "loki", "grafana", "alloy",                   # observability
            "langfuse", "faden-worker", "faden-web",  # LLM tracing
        ],
        ContainerClass.SYSTEM_CORE,
        "platform",
    ),
    # SYSTEM_AUX — application services (checked before MCP so that
    # "faden-mcp-migrator" doesn't accidentally match the generic
    # "mcp-" pattern below).
    (
        [
            "faden-gateway",
            "faden_gateway",
            "faden-backend",
            "faden-frontend",
            "faden-mcp",
            "faden-skills",
            "faden-pii",
            "faden-rag",
            "faden-auth",
            "faden-mailer",
            "faden-docling",
            "faden-ee",
            "migrator",
        ],
        ContainerClass.SYSTEM_AUX,
        "platform",
    ),
    # MCP — standalone Model Context Protocol servers
    (
        ["mcp-brave", "mcp-searxng", "mcp-"],
        ContainerClass.MCP,
        "platform",
    ),
]


def _classify_by_name(name: str) -> tuple[ContainerClass, str]:
    """Return (container_class, owner_scope) based on well-known name patterns."""
    lower = name.lower()
    for substrings, cls, scope in _AUTO_CLASS_RULES:
        if any(s in lower for s in substrings):
            return cls, scope
    return ContainerClass.UNTRUSTED, "unknown"


def _docker_refused(exc: DockerError, doing: str) -> HTTPException:
    """What Docker refused, as an answer: its reason, not a 500."""
    code = exc.status if exc.status in (400, 404, 409) else status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=code, detail=f"{doing}: {exc.message}")


async def _image_of(docker: DockerService, raw: dict[str, Any]) -> dict[str, Any]:
    """The image a container was made from; empty when it is gone."""
    try:
        return await docker.inspect_image(raw.get("Image", ""))
    except DockerError:
        return {}


def _bindings(
    ports: list[PortBinding], old: dict[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    """The form's port bindings in Docker's form, each keeping the host address it had."""
    bindings: dict[str, list[dict[str, str]]] = {}
    for p in ports:
        same = [b for b in old.get(p.container_port, []) if b.get("HostPort") == p.host_port]
        host_ip = same[0].get("HostIp", "") if same else ""
        bindings.setdefault(p.container_port, []).append({"HostIp": host_ip, "HostPort": p.host_port})
    return bindings


def _format_endpoint(ports: list[dict[str, Any]]) -> str:
    """Build a human-readable endpoint string from Docker port entries."""
    seen: list[str] = []
    for p in ports:
        pub = p.get("PublicPort")
        priv = p.get("PrivatePort")
        ip = p.get("IP", "0.0.0.0")
        if pub:
            if ip in ("::", "0.0.0.0"):
                ip = "0.0.0.0"
            entry = f"{ip}:{pub}"
            if entry not in seen:
                seen.append(entry)
        elif priv:
            entry = f":{priv}"
            if entry not in seen:
                seen.append(entry)
    return ", ".join(seen)


def _container_summary_from_docker(
    raw: dict[str, Any],
    container_class: ContainerClass = ContainerClass.UNTRUSTED,
    policy: ContainerPolicy = ContainerPolicy.FREE,
    owner_scope: str = "unknown",
) -> ContainerSummaryDTO:
    names: list[str] = raw.get("Names", [raw.get("Name", "")])
    name = names[0].lstrip("/") if names else raw.get("Id", "")[:12]
    raw_ports = raw.get("Ports")
    ports: list[dict[str, Any]] = list(raw_ports) if isinstance(raw_ports, list) else []
    network_settings = raw.get("NetworkSettings") or {}
    raw_networks = network_settings.get("Networks")
    if not isinstance(raw_networks, dict):
        raw_networks = {}
    network_names = list(raw_networks.keys())
    # Docker's list gives State and Status as strings and Image as the name;
    # its inspect -- what create returns -- nests State and gives the image's
    # sha256 as Image. Creating a container answered 500 after creating and
    # starting it, so the console said it had failed (2026-09-25).
    state = raw.get("State", "")
    status = raw.get("Status", "")
    if isinstance(state, dict):
        state = status = state.get("Status", "")
    image = (raw.get("Config") or {}).get("Image") or raw.get("Image", "")
    return ContainerSummaryDTO(
        id=raw.get("Id", ""),
        name=name,
        image=image,
        status=status,
        state=state,
        created=str(raw.get("Created", "")),
        ports=ports,
        networks=network_names,
        endpoint=_format_endpoint(ports),
        container_class=container_class,
        policy=policy,
        owner_scope=owner_scope,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/", response_model=ContainerSummaryDTO, status_code=201, name="create_container")
async def create_container(
    body: CreateContainerRequest,
    # A container is made with any image, binds and network: superusers only.
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    root_mode: bool = Depends(get_root_mode_active),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ContainerSummaryDTO:
    """Create a new container and register it in the classification registry.

    One with root on the host (any host folder bound, the Docker socket among
    them; the host's network) is made only in Root Mode. Named volumes need
    nothing.
    """
    reasons = host_access.host_access(body.volumes, body.network)
    await _refuse_host_access(
        reasons, root_mode=root_mode, doing="create", audit="container.create",
        target_id=body.name or body.image, user=user, audit_dao=audit_dao,
    )
    # Build Docker port binding config: {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]}
    port_bindings: dict[str, list[dict[str, str]]] | None = None
    if body.ports:
        port_bindings = {}
        for pb in body.ports:
            port_bindings.setdefault(pb.container_port, []).append({"HostIp": "", "HostPort": pb.host_port})

    try:
        raw = await docker.create_container(
            image=body.image,
            name=body.name or None,
            cmd=body.cmd or None,
            env=body.env or None,
            ports=port_bindings,
            volumes=body.volumes or None,
            network=body.network or None,
            auto_start=body.auto_start,
        )
    except DockerError as exc:
        # Docker creates from images it holds; it does not fetch one. That was a bare 500.
        if exc.status == status.HTTP_404_NOT_FOUND and "no such image" in str(exc.message).lower():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"The image {body.image} is not on this server. Pull it first (Containers, Images), "
                "then create the container.",
            ) from exc
        raise _docker_refused(exc, "Creating the container") from exc

    container_id: str = raw.get("Id", "")
    # Determine the name Docker assigned
    raw_name = (body.name or container_id[:12]).lstrip("/")

    entry = await registry_dao.upsert(
        container_id=container_id,
        name=raw_name,
        container_class=body.container_class,
        owner_scope=body.owner_scope,
        policy=body.policy,
        created_by=user.id,
    )

    await audit_action(
        action="container.create",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_ROOT_MODE_SEVERITY if reasons else _severity(root_mode),
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "image": body.image,
                "name": raw_name,
                "class": body.container_class.value,
                "policy": body.policy.value,
                **({"host_access": reasons} if reasons else {}),
            }
        ),
    )

    return _container_summary_from_docker(raw, entry.container_class, entry.policy, entry.owner_scope)


@router.get("/", response_model=list[ContainerSummaryDTO], name="list_containers")
async def list_containers(
    filter_class: ContainerClass | None = Query(default=None, alias="class"),
    docker: DockerService = Depends(get_docker),
    registry_dao: ContainerRegistryDAO = Depends(),
    _user: User = Depends(require_permission("containers", "read")),
) -> list[ContainerSummaryDTO]:
    """
    Return all containers visible on the connected engine.

    Merges live Docker data with the server-side registry classification.
    """
    raw_containers = await docker.list_containers(all_=True)
    registry_map = {r.container_id: r for r in await registry_dao.list_all()}

    result: list[ContainerSummaryDTO] = []
    for raw in raw_containers:
        cid = raw.get("Id", "")
        entry = registry_map.get(cid)

        if entry is None:
            # Auto-classify unregistered containers by their name
            names: list[str] = raw.get("Names", [raw.get("Name", "")])
            raw_name = names[0].lstrip("/") if names else cid[:12]
            auto_cls, auto_scope = _classify_by_name(raw_name)
            # Persist so we don't re-classify every time
            entry = await registry_dao.upsert(
                container_id=cid,
                name=raw_name,
                container_class=auto_cls,
                owner_scope=auto_scope,
                policy=ContainerPolicy.FREE,
            )

        cls = entry.container_class
        pol = entry.policy
        scope = entry.owner_scope

        if filter_class and cls != filter_class:
            continue

        result.append(_container_summary_from_docker(raw, cls, pol, scope))
    return result


@router.get("/{container_id}", response_model=ContainerDetailDTO, name="get_container")
async def get_container(
    container_id: str,
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    _user: User = Depends(require_permission("containers", "read")),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
) -> ContainerDetailDTO:
    """Return full inspect data for a single container."""
    enforcer.enforce(Action.VIEW, entry.container_class, entry.policy, root_mode)
    raw = await docker.inspect_container(container_id)
    raw_state = raw.get("State", {})
    raw_config = raw.get("Config", {})
    names = [raw.get("Name", "").lstrip("/")]
    network_settings = raw.get("NetworkSettings") or {}
    raw_port_map = network_settings.get("Ports")
    if not isinstance(raw_port_map, dict):
        raw_port_map = {}
    raw_networks = network_settings.get("Networks")
    if not isinstance(raw_networks, dict):
        raw_networks = {}
    ports = list(raw_port_map.keys())
    networks = list(raw_networks.keys())
    return ContainerDetailDTO(
        id=raw.get("Id", ""),
        name=names[0] if names else container_id[:12],
        image=raw_config.get("Image", ""),
        status=raw_state.get("Status", ""),
        state=raw_state.get("Status", ""),
        created=raw.get("Created", ""),
        ports=[{"port": p} for p in ports],
        networks=networks,
        container_class=entry.container_class,
        policy=entry.policy,
        owner_scope=entry.owner_scope,
        raw=raw,
    )


@router.get("/{container_id}/settings", response_model=ContainerSettingsDTO, name="container_settings")
async def container_settings(
    container_id: str,
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    _user: User = Depends(require_permission("containers", "read")),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
) -> ContainerSettingsDTO:
    """What the container was created with, for its edit form."""
    enforcer.enforce(Action.VIEW, entry.container_class, entry.policy, root_mode)
    try:
        raw = await docker.inspect_container(container_id)
    except DockerError as exc:
        raise _docker_refused(exc, "The container could not be read") from None
    own = recreate.own_settings(raw, await _image_of(docker, raw))
    labels = (raw.get("Config") or {}).get("Labels") or {}
    return ContainerSettingsDTO(
        image=own.image,
        name=own.name,
        cmd=own.cmd,
        env=own.env,
        ports=[
            PortBinding(host_port=b.get("HostPort", ""), container_port=port)
            for port, binds in own.ports.items() for b in binds
        ],
        volumes=own.volumes,
        network=own.network,
        running=own.start,
        container_class=entry.container_class,
        owner_scope=entry.owner_scope,
        policy=entry.policy,
        managed_by_compose="com.docker.compose.project" in labels,
        backend_runs_here=raw.get("Id") == recreate.own_container_id(),
    )


@router.put("/{container_id}", response_model=ContainerSummaryDTO, name="update_container")
async def update_container(
    container_id: str,
    body: CreateContainerRequest,
    # The new container takes any image, binds and network: superusers only.
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ContainerSummaryDTO:
    """Edit a container: it is replaced by one made from its settings and the edits.

    Docker changes none of the settings the form edits in a container that
    exists. The new container has a new id; the old one is removed once the
    new one runs, and is back as it was if the new one cannot be made.
    A new container with root on the host, from the form or from what the
    old one kept, is made only in Root Mode; so is a new class for a system
    container.
    """
    try:
        enforcer.enforce(Action.UPDATE_CONFIG, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action="container.update",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    try:
        raw = await docker.inspect_container(container_id)
    except DockerError as exc:
        raise _docker_refused(exc, "The container could not be read") from None
    if raw.get("Id") == recreate.own_container_id():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The backend runs in this container and cannot replace it. Change its settings "
                "in .env and run faden upgrade."
            ),
        )
    reasons = host_access.host_access(body.volumes, body.network, raw.get("HostConfig") or {})
    await _refuse_host_access(
        reasons, root_mode=root_mode, doing="change", audit="container.update",
        target_id=container_id, user=user, audit_dao=audit_dao,
    )
    # The new container takes the form's class: the same as registering it.
    await _refuse_system_change(
        await _classification(raw, container_id, registry_dao), body.container_class, body.policy,
        root_mode=root_mode, audit="container.update", target_id=container_id, user=user, audit_dao=audit_dao,
    )
    image = await _image_of(docker, raw)
    old = recreate.own_settings(raw, image)
    edit = recreate.ContainerEdit(
        image=body.image,
        name=(body.name or old.name).lstrip("/"),
        cmd=body.cmd or None,
        env=body.env,
        ports=_bindings(body.ports, old.ports),
        volumes=body.volumes,
        network=body.network or "",
        start=body.auto_start,
    )
    config, others = recreate.replacement(raw, image, edit)
    try:
        new = await recreate.replace(docker, raw, config, others, name=edit.name, start=edit.start)
    except DockerError as exc:
        raise _docker_refused(exc, "The container was not changed") from None

    # The class and policy go with it to its new id.
    new_id = new.get("Id", "")
    try:
        await registry_dao.delete(raw["Id"])
        entry = await registry_dao.upsert(
            container_id=new_id,
            name=edit.name,
            container_class=body.container_class,
            owner_scope=body.owner_scope,
            policy=body.policy,
            created_by=entry.created_by or user.id,
        )
        await audit_action(
            action="container.update",
            target_type="container",
            target_id=new_id,
            result=AuditResult.ALLOW,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
            metadata_json=json.dumps({
                "replaced": raw["Id"],
                "image": [old.image, edit.image],
                "name": [old.name, edit.name],
                # Names only: values may be secrets.
                "env": sorted(item.split("=", 1)[0] for item in edit.env),
                "class": body.container_class.value,
                "policy": body.policy.value,
                **({"host_access": reasons} if reasons else {}),
            }),
        )
    except Exception:
        # Replaced all the same: listed again, it is classified by its name.
        logger.exception(
            "Container %s was replaced by %s; its registry entry was not moved", raw["Id"][:12], new_id[:12],
        )
    return _container_summary_from_docker(new, entry.container_class, entry.policy, entry.owner_scope)


@router.post(
    "/{container_id}/{action}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="container_lifecycle",
)
async def container_lifecycle(
    container_id: str,
    action: str,
    user: User = Depends(_lifecycle_user),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """
    Perform a lifecycle action (start/stop/restart/pause/unpause) on a container.

    The action needs its own permission (``_LIFECYCLE_PERMISSION``) and is
    policy-enforced before execution.
    """
    policy_action = _LIFECYCLE_ACTION_MAP[action]  # an unknown one was refused by _lifecycle_user

    try:
        enforcer.enforce(policy_action, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action=f"container.{action}",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    match action:
        case "start":
            await docker.start(container_id)
        case "stop":
            await docker.stop(container_id)
        case "restart":
            await docker.restart(container_id)
        case "pause":
            await docker.pause(container_id)
        case "unpause":
            await docker.unpause(container_id)

    await audit_action(
        action=f"container.{action}",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )


@router.get("/{container_id}/logs", name="container_logs")
async def container_logs(
    container_id: str,
    tail: int = Query(default=100, ge=1, le=10000),
    follow: bool = Query(default=False),
    user: User = Depends(require_permission("containers", "logs")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> StreamingResponse:
    """
    Stream container logs.

    Supports tail= and follow= query parameters.
    """
    enforcer.enforce(Action.LOGS_VIEW, entry.container_class, entry.policy, root_mode)

    await audit_action(
        action="container.logs.view",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )

    async def _log_stream() -> Any:
        async for line in docker.logs(container_id, tail=tail, follow=follow):
            yield line

    return StreamingResponse(_log_stream(), media_type="text/plain")


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="delete_container",
)
async def delete_container(
    container_id: str,
    force: bool = Query(default=False),
    user: User = Depends(require_permission("containers", "delete")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a container (requires policy permission)."""
    try:
        enforcer.enforce(Action.DELETE, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action="container.delete",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    try:
        await docker.delete(container_id, force=force)
    except DockerError as exc:
        if exc.status == status.HTTP_409_CONFLICT and not force:
            # It answered 500: Docker refuses to remove a running container.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The container is running: stop it before you delete it.",
            ) from None
        raise _docker_refused(exc, "The container was not deleted") from None
    await registry_dao.delete(container_id)

    await audit_action(
        action="container.delete",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )


@router.put("/{container_id}/register", name="register_container")
async def register_container(
    container_id: str,
    body: RegisterContainerRequest,
    # The class decides what may be done to the container (a system container
    # made TENANT_APP may be exec'd into): superusers only.
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    root_mode: bool = Depends(get_root_mode_active),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ContainerSummaryDTO:
    """
    Manually classify/register a container in the server-side registry.

    Backend ignores any client-supplied dgx.* labels; only this endpoint
    sets the authoritative container class. A container with root on the
    host (the Docker socket, a host folder, the host's network, ...) is
    classified only in Root Mode, whatever the class: as ``TENANT_APP`` it
    could be exec'd into. So is a system container given another class.
    """
    try:
        raw = await docker.inspect_container(container_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Container {container_id!r} not found on engine.",
        ) from exc

    # Under the full id the list and the other routes look it up by, also
    # when the path names it by its name or a short id.
    full_id: str = raw.get("Id") or container_id
    name = raw.get("Name", "").lstrip("/") or container_id[:12]
    reasons = host_access.container_reasons(raw)
    await _refuse_host_access(
        reasons, root_mode=root_mode, doing="reclassify", audit="container.register",
        target_id=full_id, user=user, audit_dao=audit_dao,
    )
    await _refuse_system_change(
        await _classification(raw, container_id, registry_dao), body.container_class, body.policy,
        root_mode=root_mode, audit="container.register", target_id=full_id, user=user, audit_dao=audit_dao,
    )
    entry = await registry_dao.upsert(
        container_id=full_id,
        name=name,
        container_class=body.container_class,
        owner_scope=body.owner_scope,
        policy=body.policy,
        created_by=user.id,
    )

    await audit_action(
        action="container.register",
        target_type="container",
        target_id=full_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_ROOT_MODE_SEVERITY if reasons else _severity(root_mode),
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "class": body.container_class.value,
                "policy": body.policy.value,
                "owner_scope": body.owner_scope,
                **({"host_access": reasons} if reasons else {}),
            }
        ),
    )

    raw_list = await docker.list_containers(all_=True)
    raw_c = next((c for c in raw_list if c.get("Id", "") == full_id), {})
    return _container_summary_from_docker(
        raw_c or {"Id": full_id, "Name": name},
        entry.container_class,
        entry.policy,
        entry.owner_scope,
    )
