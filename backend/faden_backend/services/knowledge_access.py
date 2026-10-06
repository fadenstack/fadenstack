"""Which knowledge collections a person may read, and the token the gateway reads them with.

A collection is open to everyone, or restricted to the people and groups it is granted to. A restricted
collection hides everything below it: a folder of HR documents keeps its sub-folders to itself even when
one of them is open to everyone. Documents in no collection are everyone's.

Superusers and the knowledge base's managers (``rag.search:write``: admin, rag_manager, rag_editor) see
every collection -- they keep it. An agent narrows what its user sees to the collections it was given
(``AgentVersion.knowledge``) and what is below them, and sees nothing outside a collection.

The gateway searches for whoever sent the chat -- a console sign-in, an API token, an agent -- and the
backend takes only console sign-ins. So the gateway, which has checked the caller, hands the backend a
token of a minute that names the person and, for an agent, its collections: audience :data:`AUDIENCE`,
which no other endpoint accepts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rag_lite_dao import CollectionScope
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.groups import UserGroup
from faden_backend.db.models.rag_lite import (
    RagLiteCollection,
    RagLiteCollectionAccess,
    RagLiteCollectionGrant,
)
from faden_backend.db.models.users import User, api_users
from faden_backend.settings import settings

log = logging.getLogger(__name__)

#: The audience of the gateway's knowledge tokens; no sign-in carries it.
AUDIENCE = "faden:knowledge"

#: The signed-in person, if any (tests override it).
optional_active_user = api_users.current_user(active=True, optional=True)


@dataclass(frozen=True)
class KnowledgeReader:
    """Who is reading the knowledge base."""

    user_id: uuid.UUID
    group_ids: frozenset[uuid.UUID] = frozenset()
    #: Superuser or manager: every collection.
    sees_all: bool = False
    #: An agent's collections: only these and what is below them, nothing unfiled.
    only: frozenset[uuid.UUID] | None = None


async def reader_for(
    session: AsyncSession, user: User, *, only: frozenset[uuid.UUID] | None = None,
) -> KnowledgeReader:
    """*user* as a reader of the knowledge base (and, for an agent, within *only*)."""
    groups = (await session.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id))).scalars()
    manager = bool(user.is_superuser) or await RbacDAO(session).has_permission(user.id, "rag.search", "write")
    return KnowledgeReader(user.id, frozenset(groups), manager, only)


async def visible_collections(session: AsyncSession, reader: KnowledgeReader) -> tuple[set[uuid.UUID], int]:
    """The collections *reader* may read, and how many there are in all."""
    rows = (
        await session.execute(
            select(RagLiteCollection.id, RagLiteCollection.parent_id, RagLiteCollection.access),
        )
    ).all()
    parent = {r.id: r.parent_id for r in rows}
    if reader.sees_all:
        open_ = set(parent)
    else:
        restricted = {r.id for r in rows if r.access != RagLiteCollectionAccess.EVERYONE.value}
        open_ = set(parent) - restricted
        if restricted:
            mine = [and_(RagLiteCollectionGrant.principal_type == "user",
                         RagLiteCollectionGrant.principal_id == reader.user_id)]
            if reader.group_ids:
                mine.append(and_(RagLiteCollectionGrant.principal_type == "group",
                                 RagLiteCollectionGrant.principal_id.in_(reader.group_ids)))
            granted = await session.execute(
                select(RagLiteCollectionGrant.collection_id).where(
                    RagLiteCollectionGrant.collection_id.in_(restricted), or_(*mine),
                ),
            )
            open_ |= set(granted.scalars())

    def chain(cid: uuid.UUID) -> list[uuid.UUID]:
        seen: list[uuid.UUID] = []
        at: uuid.UUID | None = cid
        while at is not None and at not in seen and at in parent:
            seen.append(at)
            at = parent[at]
        return seen

    visible = set()
    for cid in parent:
        up = chain(cid)
        if all(c in open_ for c in up) and (reader.only is None or any(c in reader.only for c in up)):
            visible.add(cid)
    return visible, len(parent)


async def search_scope(session: AsyncSession, reader: KnowledgeReader) -> CollectionScope | None:
    """What *reader*'s searches may see; ``None`` when nothing is hidden from them."""
    visible, total = await visible_collections(session, reader)
    unfiled = reader.only is None
    if unfiled and len(visible) == total:
        return None
    return CollectionScope(frozenset(visible), unfiled=unfiled)


async def may_read(session: AsyncSession, reader: KnowledgeReader, collection_id: uuid.UUID | None) -> bool:
    """Whether *reader* may read what is in *collection_id* (``None``: in no collection)."""
    if collection_id is None:
        return reader.only is None
    if reader.sees_all and reader.only is None:
        return True
    visible, _total = await visible_collections(session, reader)
    return collection_id in visible


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _knowledge_claims(token: str) -> dict[str, Any] | None:
    """The claims of a gateway knowledge token, or ``None`` when *token* is not one."""
    try:
        claims = jwt.decode(
            token,
            settings.users_secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    return claims


async def knowledge_reader(
    request: Request,
    user: Annotated[User | None, Depends(optional_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> KnowledgeReader:
    """The reader of a knowledge request: a gateway knowledge token, or a sign-in with ``rag.search:read``."""
    token = _bearer(request)
    claims = _knowledge_claims(token) if token else None
    only: frozenset[uuid.UUID] | None = None
    if claims is not None:
        try:
            user = await session.get(User, uuid.UUID(str(claims["sub"])))
        except (ValueError, TypeError):
            user = None
        listed = claims.get("collections")
        if isinstance(listed, list):
            # An agent's collections as its maintainers entered them: one that is no collection id
            # names nothing, and the agent reads only the others.
            only = frozenset(c for c in (_uuid(v) for v in listed) if c is not None)
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if not user.is_superuser and not await RbacDAO(session).has_permission(user.id, "rag.search", "read"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: rag.search:read")
    return await reader_for(session, user, only=only)
