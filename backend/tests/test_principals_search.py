r"""Finding people and groups to grant something to: the text typed is matched as typed.

``%``, ``_`` and ``\`` are no wildcards, and a search needs two characters.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.groups import Group
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.principals import find_principals


async def _user(db: AsyncSession, email: str, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="x",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


def _labels(found: list) -> set[str]:
    return {p.label for p in found}


@pytest.mark.anyio
async def test_wildcards_in_the_search_match_only_themselves(dbsession: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    await _user(dbsession, f"ann_{tag}@example.com")
    await _user(dbsession, f"annx{tag}@example.com")
    dbsession.add(Group(name=f"team_{tag}"))
    dbsession.add(Group(name=f"teamx{tag}"))
    await dbsession.flush()

    assert await find_principals(dbsession, q="%%") == []
    assert await find_principals(dbsession, q="__") == []
    assert await find_principals(dbsession, q=f"%{tag}") == []
    assert await find_principals(dbsession, q=f"\\{tag}") == []
    assert _labels(await find_principals(dbsession, q=f"n_{tag}")) == {f"ann_{tag}@example.com"}
    assert _labels(await find_principals(dbsession, q=f"m_{tag}")) == {f"team_{tag}"}
    # What is typed still matches anywhere, in any case.
    assert _labels(await find_principals(dbsession, q=f"ANNX{tag}")) == {f"annx{tag}@example.com"}


@pytest.mark.anyio
async def test_a_search_needs_two_characters_besides_spaces(dbsession: AsyncSession) -> None:
    tag = uuid.uuid4().hex[:8]
    await _user(dbsession, f"a{tag}@example.com")
    assert await find_principals(dbsession, q=" a ") == []
    assert await find_principals(dbsession, q="   ") == []
    assert _labels(await find_principals(dbsession, q=f" a{tag[:1]} ")) >= {f"a{tag}@example.com"}


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["/api/skills/principals", "/api/admin/rag/principals"])
async def test_the_principals_routes_refuse_a_wildcard_search(
    route: str, dbsession: AsyncSession, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    tag = uuid.uuid4().hex[:8]
    admin = await _user(dbsession, f"root{tag}@example.com", admin=True)
    await _user(dbsession, f"someone{tag}@example.com")
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    found = await client.get(route, params={"q": "%%"})
    assert found.status_code == status.HTTP_200_OK, found.text
    assert found.json() == []
    assert (await client.get(route, params={"q": "a"})).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    named = (await client.get(route, params={"q": f"someone{tag}"})).json()
    assert [p["label"] for p in named] == [f"someone{tag}@example.com"]
