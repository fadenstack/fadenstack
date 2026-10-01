"""``/api/skills``: the skills API for everyone, admins included.

Users see and manage skills from the chat, not only administrators from the
admin pages. RBAC decides what a user may do with skills at all:

- ``skills:read``    see and use the skills visible to them (needed for any of this)
- ``skills:create``  create and import skills of their own
- ``skills:share``   share their skills with people and groups
- ``skills:publish`` make a skill visible to the whole organization
- ``skills:manage``  administer every skill (superusers always do)

and the skills service decides what they may do with each skill, from its
owner, its grants and who sees it. Requests are forwarded to it as the user
(``SkillsPrincipal``); drafting a skill from a chat and finding people to
share with are answered here, since they need the gateway and the user list.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.groups import UserGroup
from faden_backend.db.models.users import User
from faden_backend.services.chat.gateway_client import shared_client
from faden_backend.services.principals import PrincipalOption, find_principals
from faden_backend.services.skills import drafting
from faden_backend.services.skills.client import (
    SkillsPrincipal,
    SkillsServiceClient,
    get_skills_client,
)
from faden_backend.web.api.rbac import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])

RESOURCE = "skills"
_CAPS = ("create", "share", "publish")
_COOKIE_NAME = "fapiauth"

#: What may follow ``/api/skills/`` on its way to the service: plain path
#: segments. ``..``, ``%2e`` and the like would climb out of ``/api/skills``
#: into the service's internal API, which trusts the service token alone.
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")

#: Response headers passed back to the browser.
_PASSED_HEADERS = ("content-disposition",)


async def user_caps(user: User, rbac_dao: RbacDAO) -> tuple[str, ...]:
    """What RBAC lets *user* do with skills, as the skills service's capabilities."""
    if user.is_superuser:
        return ("admin", *_CAPS)
    granted = {
        p.action for p in await rbac_dao.get_user_permissions(user.id) if p.resource == RESOURCE
    }
    caps = [cap for cap in _CAPS if cap in granted]
    if "manage" in granted:
        caps.insert(0, "admin")
    return tuple(caps)


async def skills_principal(
    user: Annotated[User, Depends(require_permission(RESOURCE, "read"))],
    rbac_dao: Annotated[RbacDAO, Depends()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillsPrincipal:
    """The signed-in user as the skills service sees them."""
    groups = (
        (await session.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id)))
        .scalars()
        .all()
    )
    return SkillsPrincipal(
        user_id=str(user.id),
        group_ids=tuple(sorted(str(g) for g in groups)),
        caps=await user_caps(user, rbac_dao),
    )


# ── Answered here ─────────────────────────────────────────────────────


@router.get("/principals", response_model=list[PrincipalOption])
async def list_principals(
    _user: Annotated[User, Depends(require_permission(RESOURCE, "share"))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    ids: Annotated[str | None, Query(max_length=4000)] = None,
) -> list[PrincipalOption]:
    """People and groups to share a skill with.

    ``q`` searches e-mail addresses and group names; ``ids``
    (``user:<id>,group:<id>``) names the ones a skill is already shared with.
    """
    return await find_principals(session, q=q, ids=ids)


class DraftRequest(BaseModel):
    """What to draft a skill from."""

    #: The chat model to write it with.
    model: str = Field(min_length=1, max_length=200)
    #: The conversation to learn from.
    session_id: str | None = Field(default=None, max_length=64)
    #: Or the messages themselves (a chat not saved as a session).
    messages: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    #: What the user wants the skill to do, in their words.
    guidance: str | None = Field(default=None, max_length=2000)
    #: A skill to revise rather than a new one.
    skill_id: uuid.UUID | None = None


class DraftResponse(BaseModel):
    """A proposed skill; nothing is saved."""

    slug: str
    name: str
    description: str
    body_markdown: str
    content: str


@router.post("/draft", response_model=DraftResponse)
async def draft_skill(
    body: DraftRequest,
    request: Request,
    _user: Annotated[User, Depends(require_permission(RESOURCE, "create"))],
    principal: Annotated[SkillsPrincipal, Depends(skills_principal)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> DraftResponse:
    """Ask the chat's model for a SKILL.md made from a conversation.

    The answer is a proposal for the user to read, change and save; this
    saves nothing (see ``services.skills.drafting``).
    """
    jwt = request.cookies.get(_COOKIE_NAME)
    if not jwt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    gateway = shared_client()
    conversation = await _conversation(gateway, body, jwt)

    current = None
    fallback = "new-skill"
    if body.skill_id is not None:
        skill = await client.get_json(f"/api/skills/{body.skill_id}", principal)
        current = drafting.skill_markdown(skill)
        fallback = str(skill.get("slug") or fallback)

    answer = await _ask_model(
        gateway,
        jwt,
        body.model,
        drafting.writer_messages(conversation, guidance=body.guidance, current=current),
    )
    draft = drafting.parse_draft(answer, fallback_name=fallback)
    if current is not None:
        draft.slug = fallback  # a revision keeps the skill's name
    if not draft.body_markdown.strip():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model answered without instructions; try again or pick another model.",
        )
    return DraftResponse(**draft.to_json())


# ── Forwarded to the skills service ───────────────────────────────────


@router.api_route("", methods=["GET", "POST"], include_in_schema=False)
async def forward_root(
    request: Request,
    principal: Annotated[SkillsPrincipal, Depends(skills_principal)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> Response:
    """List or create skills."""
    return await _forward(request, "", principal, client)


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def forward(
    path: str,
    request: Request,
    principal: Annotated[SkillsPrincipal, Depends(skills_principal)],
    client: Annotated[SkillsServiceClient, Depends(get_skills_client)],
) -> Response:
    """Everything else under ``/api/skills``: the skills service's own API."""
    if not _SAFE_PATH.match(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _forward(request, f"/{path}", principal, client)


async def _forward(
    request: Request,
    suffix: str,
    principal: SkillsPrincipal,
    client: SkillsServiceClient,
) -> Response:
    body = await request.body()
    upstream = await client.forward(
        request.method,
        f"/api/skills{suffix}",
        principal,
        params=list(request.query_params.multi_items()),
        content=body or None,
        content_type=request.headers.get("content-type") if body else None,
    )
    headers = {name: upstream.headers[name] for name in _PASSED_HEADERS if name in upstream.headers}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=headers,
    )


async def _conversation(gateway: Any, body: DraftRequest, jwt: str) -> str:
    """The conversation to draft from, as text; 422 when there is none."""
    messages = body.messages or []
    if body.session_id:
        try:
            listed = await gateway.list_messages(body.session_id, jwt, limit=200)
        except Exception as exc:
            logger.warning("Drafting a skill: could not read session %s: %s", body.session_id, exc)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That conversation could not be read.",
            ) from exc
        messages = listed.get("data", []) if isinstance(listed, dict) else list(listed or [])
    conversation = drafting.transcript(messages)
    if not conversation.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="There is no conversation to make a skill from yet.",
        )
    return conversation


async def _ask_model(gateway: Any, jwt: str, model: str, messages: list[dict[str, str]]) -> str:
    """The model's answer, asked as the user and outside any chat session."""
    payload = {"model": model, "messages": messages, "stream": False, "temperature": 0.2}
    try:
        response = await gateway.chat(payload, jwt)
    except Exception as exc:
        logger.warning("Drafting a skill: the gateway failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Gateway unavailable") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_gateway_error(response))
    try:
        return str(response.json()["choices"][0]["message"]["content"] or "")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model's answer could not be read.",
        ) from exc


def _gateway_error(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.text)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])
    return str(response.text)
