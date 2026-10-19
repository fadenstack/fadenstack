"""Admin services manifest + module lifecycle endpoints.

Returns the list of optional modules and their current status so the
frontend can show / hide UI sections dynamically.  Also provides
enable / disable endpoints that start / stop the Docker containers
belonging to a module via ``docker compose --profile <profile>``.

Every switch is recorded in the audit trail (``module.enable`` /
``module.disable``: who, when, what came of it). A module with a
``disable_warning`` is switched off only with ``confirm: true``; without it
the request is refused (409, code ``confirmation_required``) and the refusal
is recorded too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User
from faden_backend.services.module_registry import ModuleDef, module_registry
from faden_backend.services.notifications import NotificationService
from faden_backend.services.system_settings import SystemSettingsService
from faden_backend.services.docker.client import DockerService
from faden_backend.settings import settings
from faden_backend.web.api.admin.system.views import get_system_settings_service
from faden_backend.web.api.admin.dependencies import get_docker
from faden_backend.web.api.rbac import require_permission
from faden_backend.services.tls import default_httpx_verify

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────


def _resolve_compose_paths() -> tuple[str, str]:
    """Return (compose_file, env_file) as absolute paths."""
    compose_file = Path(settings.system_compose_file)
    if not compose_file.is_absolute():
        compose_file = Path.cwd() / compose_file
    compose_file = compose_file.resolve()
    env_file = compose_file.parent / ".env"
    return str(compose_file), str(env_file)


async def _run_compose(
    *args: str,
    profile: str | None = None,
) -> tuple[int, str, str]:
    """Run ``docker compose`` with the system compose file.

    Returns ``(returncode, stdout, stderr)``.
    """
    compose_file, env_file = _resolve_compose_paths()
    cmd: list[str] = [
        "docker",
        "compose",
        "-f",
        compose_file,
    ]
    if os.path.isfile(env_file):
        cmd += ["--env-file", env_file]
    if profile:
        cmd += ["--profile", profile]
    cmd += list(args)

    logger.info("Running: %s", " ".join(cmd))

    def _run() -> tuple[int, str, str]:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
        )
        return (
            result.returncode,
            result.stdout.decode(errors="replace").strip(),
            result.stderr.decode(errors="replace").strip(),
        )

    returncode, stdout, stderr = await asyncio.to_thread(_run)
    if returncode != 0:
        logger.warning(
            "docker compose exited %d: %s",
            returncode,
            stderr or stdout,
        )
    return returncode, stdout, stderr


async def _probe_health(url: str) -> str:
    """Return ``"healthy"`` or ``"unhealthy"`` for a single URL."""
    try:
        async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=5.0) as client:
            resp = await client.get(url)
            return "healthy" if resp.status_code < 400 else "unhealthy"
    except Exception:
        logger.debug("Health check failed for %s", url, exc_info=True)
        return "unhealthy"


async def _container_states(
    docker: DockerService,
    container_names: list[str],
) -> list[dict[str, str]]:
    """Return ``[{name, state}]`` for the requested container names.

    If the Docker daemon is unreachable or returns an error, every
    container is reported as ``"unknown"`` rather than letting the
    exception propagate and crash the services endpoint.
    """
    try:
        all_containers = await docker.list_containers(all_=True)
    except Exception:
        logger.warning("Docker API unreachable — reporting all containers as unknown", exc_info=True)
        return [{"name": cn, "state": "unknown"} for cn in container_names]
    # Docker container names start with "/" in the API response.
    name_map: dict[str, str] = {}
    for c in all_containers:
        for n in c.get("Names", []):
            clean = n.lstrip("/")
            name_map[clean] = c.get("State", "unknown")
    return [{"name": cn, "state": name_map.get(cn, "not_found")} for cn in container_names]


def _container_states_from_name_map(
    name_map: dict[str, str],
    container_names: list[str],
) -> list[dict[str, str]]:
    """Resolve desired container states from a precomputed Docker name->state map."""
    return [{"name": cn, "state": name_map.get(cn, "not_found")} for cn in container_names]


def _resolve_container_status(
    mod: ModuleDef,
    name_map: dict[str, str],
    docker_reachable: bool,
) -> tuple[bool, list[dict[str, str]]]:
    """Determine configured/enabled and container states for a container-type module.

    Returns ``(configured, containers)``.
    """
    configured: bool = getattr(settings, mod.settings_flag, False) if mod.settings_flag else False
    containers = (
        _container_states_from_name_map(name_map, mod.container_names)
        if docker_reachable
        else [{"name": cn, "state": "unknown"} for cn in mod.container_names]
    )
    return configured, containers


def _resolve_plugin_status(
    mod: ModuleDef,
) -> tuple[bool, bool]:
    """Determine configured/enabled for a plugin-type module.

    Returns ``(configured, enabled)``.
    """
    available = mod.is_available_fn() if mod.is_available_fn else False
    return available, available


async def _emit_module_lifecycle_alert(
    request: Request,
    *,
    module_name: str,
    action: str,
    summary: str,
    details: str,
) -> None:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            service = NotificationService(session)
            queued = await service.maybe_enqueue_admin_alert(
                subject=f"Module lifecycle error: {module_name}",
                severity="critical",
                fingerprint=f"module_lifecycle_error:{module_name}:{action}",
                summary=summary,
                details=details,
                source="faden_backend.admin.services",
            )
            if queued:
                await session.commit()
    except Exception:
        logger.exception("Failed to enqueue module lifecycle alert for %s.", module_name)


class ModuleSwitch(BaseModel):
    """What a switch request may carry."""

    #: Switching off a module with a ``disable_warning``: the person saw the warning and confirmed.
    confirm: bool = False


async def _record_switch(
    audit_dao: AuditDAO,
    user: User,
    mod: ModuleDef,
    action: str,
    result: AuditResult,
    details: dict[str, Any],
) -> None:
    """The switch in the audit trail; the writer adds who and when."""
    await audit_dao.log(
        action=f"module.{action}",
        target_type="module",
        target_id=mod.name,
        result=result,
        actor_id=user.id,
        severity="high" if mod.disable_warning else "normal",
        metadata_json=json.dumps(details, default=str)[:8000],
    )


# ── GET /services ─────────────────────────────────────────────────────


@router.get("/services")
async def list_services(
    request: Request,
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_permission("modules", "read")),
) -> JSONResponse:
    """Return the manifest of optional backend modules.

    The frontend uses this to discover which features are available so
    it can show / hide navigation items and page sections dynamically.

    The module list is populated dynamically from the
    :data:`module_registry` singleton — no module names are hardcoded.
    """
    result: list[dict[str, Any]] = []
    health_checks: list[tuple[int, asyncio.Task[str]]] = []

    # Pre-fetch all Docker container states in a single API call.
    docker_reachable = True
    name_map: dict[str, str] = {}
    try:
        all_containers = await docker.list_containers(all_=True)
        for container in all_containers:
            for raw_name in container.get("Names", []):
                name_map[raw_name.lstrip("/")] = container.get("State", "unknown")
    except Exception:
        docker_reachable = False
        logger.warning("Docker API unreachable — reporting all containers as unknown", exc_info=True)

    for mod in module_registry.list_modules():
        entry: dict[str, Any] = {
            "name": mod.name,
            "display_name": mod.display_name,
            "description": mod.description,
            "module_type": mod.module_type,
            "enterprise": mod.enterprise,
            "disable_warning": mod.disable_warning,
        }

        if mod.module_type == "plugin":
            # ── Plugin modules — status via callable ──────────
            configured, enabled = _resolve_plugin_status(mod)
            entry["configured"] = configured
            entry["enabled"] = enabled
            entry["containers"] = []
            if enabled and mod.health_fn:
                entry["status"] = "unknown"
                health_checks.append(
                    (len(result), asyncio.create_task(mod.health_fn())),
                )
            else:
                entry["status"] = "configured" if configured else "disabled"
        else:
            # ── Container modules — Docker inspection ─────────
            configured, containers = _resolve_container_status(
                mod, name_map, docker_reachable,
            )
            entry["configured"] = configured
            entry["containers"] = containers
            any_running = any(c["state"] == "running" for c in containers)

            if not any_running and configured and mod.health_url_fn:
                # No Docker container found, but the module is configured
                # via settings — probe the health endpoint as a fallback
                # (supports dev-mode where services run natively).
                entry["enabled"] = False  # updated after health check
                entry["status"] = "unknown"
                health_checks.append(
                    (len(result), asyncio.create_task(_probe_health(mod.health_url_fn()))),
                )
            elif any_running and mod.health_url_fn:
                entry["enabled"] = True
                entry["status"] = "unknown"
                health_checks.append(
                    (len(result), asyncio.create_task(_probe_health(mod.health_url_fn()))),
                )
            else:
                entry["enabled"] = any_running
                entry["status"] = "configured" if configured else "disabled"

        result.append(entry)

    # Resolve pending health checks in parallel.
    if health_checks:
        statuses = await asyncio.gather(
            *(task for _, task in health_checks), return_exceptions=False,
        )
        for (idx, _), status_val in zip(health_checks, statuses):
            result[idx]["status"] = status_val
            # For container modules with no running Docker containers,
            # a successful health probe means the service is reachable
            # (e.g. running natively in dev mode).
            if status_val == "healthy" and not result[idx].get("enabled"):
                result[idx]["enabled"] = True

    return JSONResponse(status_code=200, content={"services": result})


# ── PUT /services/{name}/enable ───────────────────────────────────────


@router.put("/services/{name}/enable")
async def enable_module(
    name: str,
    request: Request,
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
    system_settings: SystemSettingsService = Depends(get_system_settings_service),
    audit_dao: AuditDAO = Depends(),
) -> JSONResponse:
    """Bring up all containers belonging to a module (container-type)
    or invoke its ``on_enable`` callback (plugin-type).
    """
    mod = module_registry.get_module(name)
    if mod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module: {name}",
        )

    errors: list[str] = []

    # ── Container modules: docker compose up ──────────────────
    if mod.module_type == "container":
        profile = mod.compose_profile or name
        rc, stdout, stderr = await _run_compose(
            "up",
            "-d",
            "--remove-orphans",
            profile=profile,
        )

        if rc != 0:
            detail = stderr or stdout or f"docker compose exited {rc}"
            await _emit_module_lifecycle_alert(
                request,
                module_name=name,
                action="enable",
                summary=f"Failed to enable module '{name}'.",
                details=detail,
            )
            await _record_switch(audit_dao, user, mod, "enable", AuditResult.ALLOW, {"errors": [detail]})
            return JSONResponse(
                status_code=200,
                content={
                    "module": name,
                    "action": "enable",
                    "started": [],
                    "errors": [detail],
                },
            )

    # ── Lifecycle callback (both types) ───────────────────────
    if mod.on_enable:
        errors.extend(
            await mod.on_enable(system_settings, True, user.id),
        )

    if errors:
        await _emit_module_lifecycle_alert(
            request,
            module_name=name,
            action="enable_sync",
            summary=f"Failed to finalize module enable for '{name}'.",
            details="; ".join(errors),
        )

    # Refresh container states for the response.
    if mod.module_type == "container":
        containers = await _container_states(docker, mod.container_names)
        started = [c["name"] for c in containers if c["state"] == "running"]
    else:
        started = []

    await _record_switch(
        audit_dao, user, mod, "enable", AuditResult.ALLOW, {"started": started, "errors": errors},
    )
    return JSONResponse(
        status_code=200,
        content={
            "module": name,
            "action": "enable",
            "started": started,
            "errors": errors,
        },
    )


# ── PUT /services/{name}/disable ──────────────────────────────────────


@router.put("/services/{name}/disable")
async def disable_module(
    name: str,
    request: Request,
    switch: ModuleSwitch | None = None,
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
    system_settings: SystemSettingsService = Depends(get_system_settings_service),
    audit_dao: AuditDAO = Depends(),
) -> JSONResponse:
    """Stop all containers belonging to a module (container-type)
    or invoke its ``on_disable`` callback (plugin-type).

    A module with a ``disable_warning`` is switched off only with
    ``{"confirm": true}``; without it nothing changes, the refusal (409,
    code ``confirmation_required``) carries the warning, and it is recorded.
    """
    mod = module_registry.get_module(name)
    if mod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module: {name}",
        )

    confirmed = bool(switch and switch.confirm)
    recorded: dict[str, Any] = (
        {"confirmed": confirmed, "warning": mod.disable_warning} if mod.disable_warning else {}
    )
    if mod.disable_warning and not confirmed:
        await _record_switch(
            audit_dao, user, mod, "disable", AuditResult.DENY, {**recorded, "reason": "not confirmed"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "confirmation_required",
                "message": f"{mod.disable_warning} To switch it off, confirm it (send confirm: true).",
            },
        )

    # ── Lifecycle callback (run *before* stopping containers) ─
    errors: list[str] = []
    if mod.on_disable:
        errors = await mod.on_disable(system_settings, False, user.id)
        if errors:
            await _emit_module_lifecycle_alert(
                request,
                module_name=name,
                action="disable_sync",
                summary=f"Failed to sync module disable for '{name}'.",
                details="; ".join(errors),
            )
            await _record_switch(
                audit_dao, user, mod, "disable", AuditResult.ALLOW, {**recorded, "errors": errors},
            )
            return JSONResponse(
                status_code=200,
                content={
                    "module": name,
                    "action": "disable",
                    "stopped": [],
                    "errors": errors,
                },
            )

    # ── Container modules: docker compose stop ────────────────
    if mod.module_type == "container":
        profile = mod.compose_profile or name
        rc, stdout, stderr = await _run_compose(
            "stop",
            *mod.compose_services,
            profile=profile,
        )

        if rc != 0:
            detail = stderr or stdout or f"docker compose exited {rc}"
            await _emit_module_lifecycle_alert(
                request,
                module_name=name,
                action="disable",
                summary=f"Failed to disable module '{name}'.",
                details=detail,
            )
            await _record_switch(
                audit_dao, user, mod, "disable", AuditResult.ALLOW, {**recorded, "errors": [detail]},
            )
            return JSONResponse(
                status_code=200,
                content={
                    "module": name,
                    "action": "disable",
                    "stopped": [],
                    "errors": [detail],
                },
            )

        containers = await _container_states(docker, mod.container_names)
        stopped = [c["name"] for c in containers if c["state"] != "running"]
    else:
        stopped = []

    await _record_switch(
        audit_dao, user, mod, "disable", AuditResult.ALLOW, {**recorded, "stopped": stopped, "errors": []},
    )
    return JSONResponse(
        status_code=200,
        content={
            "module": name,
            "action": "disable",
            "stopped": stopped,
            "errors": [],
        },
    )


# ── Container logs for a module ───────────────────────────────────────


@router.get("/services/{name}/logs/{container_name}", name="module_container_logs")
async def module_container_logs(
    name: str,
    container_name: str,
    tail: int = Query(default=200, ge=1, le=10000),
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
) -> StreamingResponse:
    """Stream logs for a specific container belonging to a module.

    The *container_name* must be one of the containers declared in the
    module definition.  This prevents arbitrary container access through
    this simplified endpoint.
    """
    mod = module_registry.get_module(name)
    if not mod:
        raise HTTPException(status_code=404, detail=f"Unknown module: {name}")

    if mod.module_type == "plugin":
        raise HTTPException(
            status_code=400,
            detail="Plugin modules do not have containers.",
        )

    allowed_containers: list[str] = mod.container_names
    if container_name not in allowed_containers:
        raise HTTPException(
            status_code=404,
            detail=f"Container '{container_name}' is not part of module '{name}'.",
        )

    # Resolve the Docker container ID from the name.
    try:
        all_containers = await docker.list_containers(all_=True)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Docker API is unreachable.",
        )

    container_id: str | None = None
    for c in all_containers:
        names = [n.lstrip("/") for n in c.get("Names", [])]
        if container_name in names:
            container_id = c.get("Id")
            break

    if not container_id:
        raise HTTPException(
            status_code=404,
            detail=f"Container '{container_name}' is not running or does not exist.",
        )

    async def _stream() -> Any:
        async for line in docker.logs(container_id, tail=tail, follow=False):
            yield line

    return StreamingResponse(_stream(), media_type="text/plain")
