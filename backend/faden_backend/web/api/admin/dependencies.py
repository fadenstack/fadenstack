"""Shared FastAPI dependencies for the /admin namespace."""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from faden_backend.db.dao.root_session_dao import RootSessionDAO
from faden_backend.db.models.containers import (
    AuditResult,
    ContainerClass,
    ContainerPolicy,
    ContainerRegistry,
)
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.docker.client import DockerService
from faden_backend.services.policy.enforcement import PolicyEnforcer
from faden_backend.settings import settings

#: The internal token of the dev environment, where nothing generates one.
DEV_INTERNAL_TOKEN = "dev-internal-token"  # noqa: S105


def get_docker(request: Request) -> DockerService:
    """Retrieve the shared DockerService from app state."""
    return request.app.state.docker  # type: ignore[no-any-return]


def get_policy_enforcer() -> PolicyEnforcer:
    """Return a stateless PolicyEnforcer instance."""
    return PolicyEnforcer()


async def require_superuser(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    """Ensure the current user is a superuser (admin)."""
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return user


def internal_token() -> str:
    """The token the platform's own services present to the backend; empty: none is set."""
    if settings.internal_token:
        return settings.internal_token
    return DEV_INTERNAL_TOKEN if settings.environment == "dev" else ""


async def require_internal_token(request: Request) -> None:
    """Let only a platform service in: ``Authorization: Bearer <internal token>``.

    Without a token configured every call is refused (503), never let through: these routes are
    reachable from the same port as the console's.
    """
    expected = internal_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal calls are not configured.",
        )
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token.",
        )


async def get_root_mode_active(
    user: Annotated[User, Depends(require_superuser)],
    root_dao: RootSessionDAO = Depends(),
) -> bool:
    """Return True if the current user has a valid active root session."""
    session = await root_dao.get_active(user.id)
    return session is not None


async def get_registry_entry(
    container_id: str,
    registry_dao: ContainerRegistryDAO = Depends(),
) -> ContainerRegistry:
    """Fetch a container registry entry or return a default UNTRUSTED entry."""
    entry = await registry_dao.get(container_id)
    if entry is None:
        # Unknown containers are treated as UNTRUSTED with restricted policy
        return ContainerRegistry(
            container_id=container_id,
            name=container_id,
            container_class=ContainerClass.UNTRUSTED,
            owner_scope="unknown",
            policy=ContainerPolicy.RESTRICTED,
            engine_id="local",
        )
    return entry


async def audit_action(
    action: str,
    target_type: str,
    target_id: str,
    result: AuditResult,
    actor_id: uuid.UUID | None,
    severity: str,
    audit_dao: AuditDAO,
    metadata_json: str | None = None,
) -> None:
    """Helper that logs an audit event and flushes it."""
    await audit_dao.log(
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        actor_id=actor_id,
        severity=severity,
        metadata_json=metadata_json,
    )
