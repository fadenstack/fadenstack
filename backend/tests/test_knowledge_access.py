"""Who reads which knowledge collection, and who changes the knowledge base.

A collection is open to everyone or to the people and groups it is granted to; a restricted one hides
what is below it. Everyone else gets "not found", never "forbidden": a hidden collection is not there
for them. Only the knowledge base's managers change it -- ``default_user`` could create, change and
delete anything in it through the API.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rag_lite_dao import RagLiteChunkDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.rag_lite import (
    MAX_EMBEDDING_DIM,
    RagLiteChunk,
    RagLiteCollection,
    RagLiteCollectionGrant,
    RagLiteDocument,
    RagLiteDocumentStatus,
)
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import knowledge_access
from faden_backend.settings import settings

API = "/api/admin/rag"
#: Every chunk's and the query's: each chunk is as near as the next, so a search returns all it may see.
_VECTOR = [1.0] * MAX_EMBEDDING_DIM


@dataclass
class World:
    manager: User
    alice: User  # in HR
    bob: User
    handbook: RagLiteCollection
    hr: RagLiteCollection
    pay: RagLiteCollection  # below HR, itself open to everyone
    handbook_doc: RagLiteDocument
    hr_doc: RagLiteDocument
    pay_doc: RagLiteDocument
    unfiled_doc: RagLiteDocument


async def _user(dbsession: AsyncSession, rbac: RbacDAO, role: str) -> User:
    user = User(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(user)
    await dbsession.flush()
    await rbac.assign_role(user.id, (await rbac.get_role_by_name(role)).id)
    return user


def _doc(collection: RagLiteCollection | None, name: str, text: str) -> tuple[RagLiteDocument, RagLiteChunk]:
    doc = RagLiteDocument(
        id=uuid.uuid4(),
        collection_id=collection.id if collection else None,
        filename=name,
        doc_type="txt",
        size_bytes=len(text),
        sha256=uuid.uuid4().hex,
        chunk_count=1,
        status=RagLiteDocumentStatus.READY,
    )
    chunk = RagLiteChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        collection_id=doc.collection_id,
        chunk_index=0,
        chunk_text=text,
        embedding=_VECTOR,
        embedding_dim=len(_VECTOR),
    )
    return doc, chunk


@pytest.fixture
async def world(dbsession: AsyncSession) -> World:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    manager = await _user(dbsession, rbac, "rag_editor")
    alice = await _user(dbsession, rbac, "default_user")
    bob = await _user(dbsession, rbac, "default_user")
    group = Group(id=uuid.uuid4(), name=f"hr-{uuid.uuid4().hex[:6]}")
    dbsession.add(group)
    await dbsession.flush()
    dbsession.add(UserGroup(user_id=alice.id, group_id=group.id))

    handbook = RagLiteCollection(id=uuid.uuid4(), name="Handbook")
    hr = RagLiteCollection(id=uuid.uuid4(), name="HR", access="restricted")
    dbsession.add_all([handbook, hr])
    await dbsession.flush()
    pay = RagLiteCollection(id=uuid.uuid4(), name="Pay", parent_id=hr.id)
    dbsession.add(pay)
    await dbsession.flush()
    dbsession.add(
        RagLiteCollectionGrant(id=uuid.uuid4(), collection_id=hr.id, principal_type="group", principal_id=group.id),
    )
    docs = [
        _doc(handbook, "handbook.txt", "holiday rules for everyone"),
        _doc(hr, "hr.txt", "salary bands and holiday for staff"),
        _doc(pay, "pay.txt", "payroll holiday allowance"),
        _doc(None, "unfiled.txt", "holiday calendar"),
    ]
    for doc, _chunk in docs:
        dbsession.add(doc)
    await dbsession.flush()
    for _doc_row, chunk in docs:
        dbsession.add(chunk)
    await dbsession.flush()
    return World(manager, alice, bob, handbook, hr, pay, *(d for d, _ in docs))


def _as(app: FastAPI, user: User) -> None:
    app.dependency_overrides[current_active_user] = lambda: user
    app.dependency_overrides[knowledge_access.optional_active_user] = lambda: user


def _knowledge_token(user: User, collections: list[str] | None = None, *, ttl: int = 60) -> str:
    now = int(time.time())
    claims: dict = {"sub": str(user.id), "aud": knowledge_access.AUDIENCE, "iat": now, "exp": now + ttl}
    if collections is not None:
        claims["collections"] = collections
    return jwt.encode(claims, settings.users_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Who sees which collection
# ---------------------------------------------------------------------------


async def test_a_restricted_collection_hides_itself_and_what_is_below_it(
    world: World, dbsession: AsyncSession,
) -> None:
    alice = await knowledge_access.reader_for(dbsession, world.alice)
    bob = await knowledge_access.reader_for(dbsession, world.bob)
    manager = await knowledge_access.reader_for(dbsession, world.manager)

    assert (await knowledge_access.visible_collections(dbsession, bob))[0] >= {world.handbook.id}
    assert not {world.hr.id, world.pay.id} & (await knowledge_access.visible_collections(dbsession, bob))[0], (
        "Pay is open to everyone, but it is inside HR"
    )
    assert {world.handbook.id, world.hr.id, world.pay.id} <= (
        await knowledge_access.visible_collections(dbsession, alice)
    )[0], "alice is in the group HR is granted to"
    assert manager.sees_all, "a knowledge manager keeps every collection"


async def test_the_search_scope_is_none_when_nothing_is_hidden(world: World, dbsession: AsyncSession) -> None:
    manager = await knowledge_access.reader_for(dbsession, world.manager)
    assert await knowledge_access.search_scope(dbsession, manager) is None

    bob = await knowledge_access.reader_for(dbsession, world.bob)
    scope = await knowledge_access.search_scope(dbsession, bob)
    assert scope is not None and scope.unfiled
    assert world.handbook.id in scope.collections and world.hr.id not in scope.collections


async def test_a_search_finds_only_what_its_reader_may_read(world: World, dbsession: AsyncSession) -> None:
    chunks = RagLiteChunkDAO(dbsession)
    bob = await knowledge_access.reader_for(dbsession, world.bob)
    alice = await knowledge_access.reader_for(dbsession, world.alice)

    async def found(reader: knowledge_access.KnowledgeReader) -> set[str]:
        hits = await chunks.search_similar(
            query_vector=_VECTOR, top_k=10, scope=await knowledge_access.search_scope(dbsession, reader),
        )
        return {r["filename"] for r in hits}

    found_by_bob = await found(bob)
    found_by_alice = await found(alice)

    assert found_by_bob == {"handbook.txt", "unfiled.txt"}
    assert found_by_alice == {"handbook.txt", "hr.txt", "pay.txt", "unfiled.txt"}


async def test_an_empty_scope_finds_nothing_not_everything(world: World, dbsession: AsyncSession) -> None:
    """An agent given only collections that do not exist reads nothing (``collection_ids=[]`` means "all")."""
    reader = await knowledge_access.reader_for(dbsession, world.alice, only=frozenset({uuid.uuid4()}))
    scope = await knowledge_access.search_scope(dbsession, reader)
    assert scope is not None and not scope.collections and not scope.unfiled
    assert await RagLiteChunkDAO(dbsession).search_lexical("holiday", top_k=10, scope=scope) == []


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


async def test_a_hidden_document_is_not_found(world: World, fastapi_app: FastAPI, client: AsyncClient) -> None:
    _as(fastapi_app, world.bob)
    assert (await client.get(f"{API}/documents/{world.hr_doc.id}")).status_code == 404
    assert (await client.get(f"{API}/documents/{world.pay_doc.id}/passage")).status_code == 404
    assert (await client.get(f"{API}/documents/{world.handbook_doc.id}")).status_code == 200
    assert (await client.get(f"{API}/documents", params={"collection_id": str(world.hr.id)})).status_code == 404

    listed = {d["filename"] for d in (await client.get(f"{API}/documents/all")).json()}
    assert listed == {"handbook.txt", "unfiled.txt"}
    names = {c["name"] for c in (await client.get(f"{API}/collections")).json()}
    assert names == {"Handbook"}

    _as(fastapi_app, world.alice)
    assert (await client.get(f"{API}/documents/{world.pay_doc.id}")).status_code == 200


async def test_only_knowledge_managers_change_the_knowledge_base(
    world: World, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    _as(fastapi_app, world.bob)
    assert (await client.post(f"{API}/collections", json={"name": "Mine"})).status_code == 403
    assert (await client.delete(f"{API}/collections/{world.handbook.id}")).status_code == 403
    renamed = await client.patch(f"{API}/collections/{world.handbook.id}", json={"name": "x"})
    assert renamed.status_code == 403

    _as(fastapi_app, world.manager)
    made = await client.post(
        f"{API}/collections",
        json={"name": "Finance", "access": "restricted",
              "grants": [{"principal_type": "user", "principal_id": str(world.bob.id)}]},
    )
    assert made.status_code == 201, made.text
    assert made.json()["access"] == "restricted"
    assert made.json()["grants"] == [
        {"principal_type": "user", "principal_id": str(world.bob.id), "label": world.bob.email},
    ]
    unknown = await client.patch(
        f"{API}/collections/{made.json()['id']}",
        json={"grants": [{"principal_type": "group", "principal_id": str(uuid.uuid4())}]},
    )
    assert unknown.status_code == 422 and "No such person or group" in unknown.text

    listed = {c["name"]: c for c in (await client.get(f"{API}/collections")).json()}
    assert {"Handbook", "HR", "Pay", "Finance"} <= set(listed)
    assert listed["HR"]["grants"][0]["principal_type"] == "group"

    opened = await client.patch(
        f"{API}/collections/{made.json()['id']}", json={"access": "everyone"},
    )
    assert opened.json()["access"] == "everyone" and opened.json()["grants"] == []


async def test_default_user_reads_the_knowledge_base_and_changes_nothing(
    world: World, dbsession: AsyncSession,
) -> None:
    rbac = RbacDAO(dbsession)
    assert await rbac.has_permission(world.bob.id, "rag.search", "read")
    for resource, action in [
        ("rag.search", "write"), ("rag.containers", "create"), ("rag.containers", "delete"),
        ("rag.assets", "create"), ("rag.assets", "delete"), ("rag.publish", "execute"),
    ]:
        assert not await rbac.has_permission(world.bob.id, resource, action), (resource, action)


# ---------------------------------------------------------------------------
# The gateway's knowledge token
# ---------------------------------------------------------------------------


async def test_the_gateway_reads_as_the_person_it_names(
    world: World, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    fastapi_app.dependency_overrides[knowledge_access.optional_active_user] = lambda: None
    as_bob = {"Authorization": f"Bearer {_knowledge_token(world.bob)}"}
    assert (await client.get(f"{API}/documents/{world.handbook_doc.id}/passage", headers=as_bob)).status_code == 200
    assert (await client.get(f"{API}/documents/{world.hr_doc.id}/passage", headers=as_bob)).status_code == 404


async def test_an_agent_reads_only_its_collections(
    world: World, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    fastapi_app.dependency_overrides[knowledge_access.optional_active_user] = lambda: None
    agent = {"Authorization": f"Bearer {_knowledge_token(world.alice, [str(world.hr.id), 'not-an-id'])}"}

    assert (await client.get(f"{API}/documents/{world.pay_doc.id}", headers=agent)).status_code == 200
    assert (await client.get(f"{API}/documents/{world.handbook_doc.id}", headers=agent)).status_code == 404
    assert (await client.get(f"{API}/documents/{world.unfiled_doc.id}", headers=agent)).status_code == 404


async def test_a_knowledge_token_is_refused_once_expired_or_for_an_inactive_person(
    world: World, fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    fastapi_app.dependency_overrides[knowledge_access.optional_active_user] = lambda: None
    expired = {"Authorization": f"Bearer {_knowledge_token(world.bob, ttl=-5)}"}
    assert (await client.get(f"{API}/documents/{world.handbook_doc.id}", headers=expired)).status_code == 401

    world.bob.is_active = False
    await dbsession.flush()
    current = {"Authorization": f"Bearer {_knowledge_token(world.bob)}"}
    assert (await client.get(f"{API}/documents/{world.handbook_doc.id}", headers=current)).status_code == 401


async def test_a_sign_in_is_not_a_knowledge_token_and_back(world: World) -> None:
    """The knowledge audience opens the knowledge endpoints alone; a sign-in's audience is not taken as one."""
    sign_in = jwt.encode(
        {"sub": str(world.bob.id), "aud": "fastapi-users:auth", "iat": int(time.time()), "exp": int(time.time()) + 60},
        settings.users_secret,
        algorithm="HS256",
    )
    assert knowledge_access._knowledge_claims(sign_in) is None  # noqa: SLF001


async def test_an_edit_changes_only_what_it_sends(
    world: World, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    """A summary edit sends no parent, and a rename no summary: neither is refused or cleared."""
    _as(fastapi_app, world.manager)
    url = f"{API}/collections/{world.pay.id}"
    summary = await client.patch(url, json={"description": "Pay scales"})
    assert summary.status_code == 200, summary.text
    renamed = await client.patch(url, json={"name": "Payroll"})
    assert renamed.json()["description"] == "Pay scales"
    assert renamed.json()["parent_id"] == str(world.hr.id), "not moved to the top"


async def test_managers_find_people_and_groups_to_open_a_collection_to(
    world: World, fastapi_app: FastAPI, client: AsyncClient,
) -> None:
    _as(fastapi_app, world.manager)
    found = await client.get(f"{API}/principals", params={"q": world.bob.email[:12]})
    assert {"type": "user", "id": str(world.bob.id), "label": world.bob.email} in found.json()
    _as(fastapi_app, world.bob)
    assert (await client.get(f"{API}/principals", params={"q": "hr"})).status_code == 403
