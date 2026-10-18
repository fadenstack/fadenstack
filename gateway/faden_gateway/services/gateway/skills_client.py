"""HTTP client for the Skills micro-service.

Used by the gateway pipeline to resolve matching skills for a
request context via the Skills service's internal API, and by an agent's
route to list the skills its person may pick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Who a call to the skills API acts for; the service believes them next to its token (its web/api/auth.py).
HEADER_USER = "x-faden-user"
HEADER_GROUPS = "x-faden-groups"
HEADER_CAPS = "x-faden-caps"
HEADER_TENANT = "x-faden-tenant"


@dataclass(slots=True)
class ResolvedSkill:
    """A resolved skill ready for injection into the LLM pipeline."""

    skill_id: str
    name: str
    slug: str
    version: int
    body_markdown: str
    priority: int
    score: int
    allowed_tools: list[str] | None = None
    preferred_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    knowledge_sources: list[str] | None = None
    #: The version's content hash, for the audit log: what the model was told
    #: is exactly this stored version.
    content_sha256: str | None = None
    #: Why the request got it: ``explicit``, ``always``, ``trigger``, ``assignment``.
    matched_by: str | None = None
    #: The folder's other files: ``{path, size, kind, executable, text}``;
    #: ``text`` only when asked for inline and it fit.
    files: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class SkillResolveResult:
    """Result of a skill resolution call."""

    skills: list[ResolvedSkill] = field(default_factory=list)


@dataclass(slots=True)
class UsableSkill:
    """A skill a chat can be given, as a picker shows it: never its instructions."""

    id: str
    slug: str
    name: str
    description: str
    activation: str


class SkillsUnavailableError(Exception):
    """The skills service gave no list: it could not be reached, or it failed (``reached``)."""

    def __init__(self, message: str, *, reached: bool) -> None:
        super().__init__(message)
        self.reached = reached


class SkillsClient:
    """Async client wrapping Skills service internal HTTP endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        self._headers = {"Authorization": f"Bearer {service_token}"}

    async def resolve_skills(
        self,
        *,
        tenant_id: str,
        user_id: str | None = None,
        group_ids: list[str] | tuple[str, ...] | None = None,
        requested: list[str] | None = None,
        assistant_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        user_query: str | None = None,
        file_types: list[str] | None = None,
        tags: list[str] | None = None,
        max_skills: int = 3,
        inline_files_chars: int = 0,
    ) -> SkillResolveResult:
        """Resolve matching skills for a gateway request context.

        Calls ``POST /api/internal/skills/resolve``. The service resolves among
        the skills *user_id* can see: their own, the tenant's, and the ones
        shared with them or one of *group_ids*. *requested* names skills the
        user asked for (slug or id), which come whatever their triggers say.
        """
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "group_ids": list(group_ids or []),
            "requested": list(requested or []),
            "assistant_id": assistant_id,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "session_id": session_id,
            "user_query": user_query,
            "file_types": file_types or [],
            "tags": tags or [],
            "max_skills": max_skills,
            "inline_files_chars": inline_files_chars,
        }
        try:
            resp = await self._http.post(
                f"{self._base}/api/internal/skills/resolve",
                json=body,
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            skills = [
                ResolvedSkill(
                    skill_id=s["skill_id"],
                    name=s["name"],
                    slug=s["slug"],
                    version=s["version"],
                    body_markdown=s["body_markdown"],
                    priority=s["priority"],
                    score=s["score"],
                    allowed_tools=s.get("allowed_tools"),
                    preferred_tools=s.get("preferred_tools"),
                    forbidden_tools=s.get("forbidden_tools"),
                    knowledge_sources=s.get("knowledge_sources"),
                    content_sha256=s.get("content_sha256"),
                    matched_by=s.get("matched_by"),
                    files=s.get("files") or None,
                )
                for s in data.get("skills", [])
            ]
            return SkillResolveResult(skills=skills)
        except Exception:
            logger.exception("Skills resolve failed")
            return SkillResolveResult()

    async def list_usable(
        self,
        *,
        tenant_id: str,
        user_id: str,
        group_ids: list[str] | tuple[str, ...] = (),
        query: str | None = None,
        limit: int = 50,
    ) -> list[UsableSkill]:
        """The skills *user_id* may be given in a chat: what the chat's skill picker lists.

        Calls ``GET /api/skills?usable=true`` as that user -- their id and groups in the headers the service
        believes next to its token -- with no capabilities. None is needed to see a skill, and a chat is never
        resolved as an administrator: listed with ``admin``, the picker would offer other people's private
        skills, which no chat of theirs gets. Raises ``SkillsUnavailableError`` when there is no list.
        """
        params: dict[str, str | int] = {"usable": "true", "limit": limit}
        if query:
            params["q"] = query
        headers = {
            **self._headers,
            HEADER_TENANT: tenant_id,
            HEADER_USER: user_id,
            HEADER_GROUPS: ",".join(group_ids),
            HEADER_CAPS: "",
        }
        try:
            resp = await self._http.get(
                f"{self._base}/api/skills", params=params, headers=headers, timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise SkillsUnavailableError(
                f"The skills service could not be reached: {exc!r}", reached=False,
            ) from exc
        if resp.status_code >= 400:
            raise SkillsUnavailableError(f"The skills service answered {resp.status_code}.", reached=True)
        try:
            return [
                UsableSkill(
                    id=str(s["id"]),
                    slug=str(s["slug"]),
                    name=str(s["name"]),
                    description=str(s.get("description") or ""),
                    activation=str(s.get("activation") or ""),
                )
                for s in resp.json().get("items") or []
            ]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SkillsUnavailableError("The skills list could not be read.", reached=True) from exc

    async def read_file(
        self,
        *,
        skill_id: str,
        version: int,
        path: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        """One file of a resolved skill's version: ``{path, size, kind, text}``.

        Calls ``GET /api/internal/skills/{id}/versions/{n}/file``; raises when
        the service refuses (the model is told why).
        """
        resp = await self._http.get(
            f"{self._base}/api/internal/skills/{skill_id}/versions/{version}/file",
            params={"tenant_id": tenant_id, "path": path},
            headers=self._headers,
            timeout=10.0,
        )
        if resp.status_code == 404:
            raise ValueError(f"{path} could not be found in the skill.")
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def record_usage(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        version: int,
        session_id: str | None = None,
        user_id: str | None = None,
        matched_by: str | None = None,
    ) -> None:
        """Record skill usage telemetry (fire-and-forget).

        Calls ``POST /api/internal/skills/usage``.
        """
        body = {
            "tenant_id": tenant_id,
            "skill_id": skill_id,
            "version": version,
            "session_id": session_id,
            "user_id": user_id,
            "matched_by": matched_by,
        }
        try:
            resp = await self._http.post(
                f"{self._base}/api/internal/skills/usage",
                json=body,
                headers=self._headers,
                timeout=5.0,
            )
            resp.raise_for_status()
        except Exception:
            logger.debug("Skills usage recording failed", exc_info=True)
