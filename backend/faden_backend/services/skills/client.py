"""HTTP client for the skills service.

The backend is the one caller of the skills API. It signs the user in, works
out what RBAC lets them do with skills, and forwards the request with the
shared service token and that user in headers; the service decides what the
user may do with each skill (its owner, who it is shared with, who sees it).

Every call used to go out as the service alone, so the skills service could
not tell users apart: it stored no owner, and resolved the same skills for
everyone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException
from starlette import status

from llm_port_backend.services.tls import default_httpx_verify
from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)

HEADER_USER = "X-LLM-Port-User"
HEADER_GROUPS = "X-LLM-Port-Groups"
HEADER_CAPS = "X-LLM-Port-Caps"
HEADER_TENANT = "X-LLM-Port-Tenant"


@dataclass(frozen=True)
class SkillsPrincipal:
    """The user a request to the skills service acts for."""

    user_id: str
    group_ids: tuple[str, ...] = ()
    #: What RBAC lets them do: ``admin``, ``create``, ``share``, ``publish``.
    caps: tuple[str, ...] = ()
    tenant_id: str = "default"

    def headers(self) -> dict[str, str]:
        """The headers that say who they are."""
        return {
            HEADER_USER: self.user_id,
            HEADER_GROUPS: ",".join(self.group_ids),
            HEADER_CAPS: ",".join(self.caps),
            HEADER_TENANT: self.tenant_id,
        }


class SkillsServiceClient:
    """Forwards requests to the skills service."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_sec: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout_sec = timeout_sec
        self._transport = transport

    async def forward(
        self,
        method: str,
        path: str,
        principal: SkillsPrincipal | None,
        *,
        params: list[tuple[str, str]] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        """Send one request to the skills service and return its response as is."""
        if not self.service_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Skills integration is not configured (missing service token).",
            )
        headers = {"Authorization": f"Bearer {self.service_token}"}
        if principal is not None:
            headers.update(principal.headers())
        if content_type:
            headers["Content-Type"] = content_type
        async with httpx.AsyncClient(
            verify=default_httpx_verify(),
            timeout=self.timeout_sec,
            transport=self._transport,
        ) as client:
            try:
                return await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    content=content,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"Skills service timed out calling {path}.",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to reach Skills service: {exc}",
                ) from exc

    async def get_json(
        self,
        path: str,
        principal: SkillsPrincipal,
        params: list[tuple[str, str]] | None = None,
    ) -> Any:
        """GET *path* as *principal*; its JSON, or its error as an HTTPException."""
        response = await self.forward("GET", path, principal, params=params)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=_detail(response))
        return response.json()

    async def remove_principal(self, principal_type: str, principal_id: str) -> None:
        """Drop every skill grant to a deleted user or group."""
        response = await self.forward(
            "DELETE",
            f"/api/internal/principals/{principal_type}/{principal_id}",
            None,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=_detail(response))

    async def health(self) -> dict[str, Any]:
        """The service's health answer."""
        response = await self.forward("GET", "/api/health", None)
        return response.json()  # type: ignore[no-any-return]


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return response.text


def get_skills_client() -> SkillsServiceClient:
    """Factory — raises 503 when the Skills module is disabled."""
    client = skills_client_if_enabled()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Skills module is not enabled.",
        )
    return client


def skills_client_if_enabled() -> SkillsServiceClient | None:
    """The client, or ``None`` when the Skills module is off."""
    if not settings.skills_enabled:
        return None
    return SkillsServiceClient(
        base_url=settings.skills_service_url,
        service_token=settings.skills_service_token,
    )


async def forget_principal(principal_type: str, principal_id: str) -> None:
    """Best effort: a deleted user or group keeps no skill grants.

    A grant to a deleted principal matches nobody (ids are not reused), so a
    failure here leaves an unused row, not access; it is logged, not raised.
    """
    client = skills_client_if_enabled()
    if client is None or not client.service_token:
        return
    try:
        await client.remove_principal(principal_type, principal_id)
    except Exception as exc:  # deleting the user must not fail on this
        logger.warning(
            "Could not remove the skill grants of %s %s: %s",
            principal_type,
            principal_id,
            exc,
        )
