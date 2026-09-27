"""Renaming a chat and moving it between projects are saved.

A chat moved to a project in the sidebar went back to "no project" on the
next load: the update did not accept a project. It now does -- only one of
the user's own -- and null takes the chat out again.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import ChatProject, ChatSession
from llm_port_api.services.gateway.auth import AuthContext, get_auth_context

pytestmark = pytest.mark.anyio


def _as(fastapi_app: FastAPI, user: str) -> None:
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id=user, tenant_id="t1", raw_claims={},
    )


async def test_a_chat_is_renamed_and_moved_in_and_out_of_a_project(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    project = ChatProject(tenant_id="t1", user_id="u1", name="Launch")
    sess = ChatSession(tenant_id="t1", user_id="u1", title="old")
    db_session.add_all([project, sess])
    await db_session.flush()
    _as(fastapi_app, "u1")

    renamed = await client.patch(f"/v1/sessions/{sess.id}", json={"title": "Launch timeline"})
    moved = await client.patch(f"/v1/sessions/{sess.id}", json={"project_id": str(project.id)})
    assert renamed.status_code == moved.status_code == 200
    assert moved.json()["title"] == "Launch timeline"
    assert moved.json()["project_id"] == str(project.id)

    out = await client.patch(f"/v1/sessions/{sess.id}", json={"project_id": None})
    assert out.status_code == 200
    assert out.json()["project_id"] is None


async def test_a_chat_cannot_join_another_users_project(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    theirs = ChatProject(tenant_id="t1", user_id="u2", name="Someone else's")
    mine = ChatSession(tenant_id="t1", user_id="u1", title="mine")
    db_session.add_all([theirs, mine])
    await db_session.flush()
    _as(fastapi_app, "u1")

    resp = await client.patch(f"/v1/sessions/{mine.id}", json={"project_id": str(theirs.id)})

    assert resp.status_code == 404
    await db_session.refresh(mine)
    assert mine.project_id is None
