"""RAG Lite while it is off: a 409 that says to switch it on, not a 500; switched on, it works without a restart.

Found 19 November 2026 in the upgrade check to 0.7.1: an upload while RAG Lite was off ended in an unhandled
AttributeError (the service is made only when RAG Lite is on), and one switched on after the start had no service
until a restart.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rag_lite_dao import RagLiteChunkDAO
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.knowledge_access import optional_active_user
from faden_backend.services.rag_lite.service import RAG_LITE_OFF, RagLiteService, service_for
from faden_backend.services.rag_lite.tasks import rag_lite_ingest_task
from faden_backend.settings import settings

OFF = {"code": "rag_lite_off", "message": RAG_LITE_OFF}


@pytest.fixture
async def admin(dbsession: AsyncSession, fastapi_app: FastAPI) -> User:
    user = User(
        id=uuid.uuid4(), email=f"rag{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[optional_active_user] = lambda: user
    return user


@pytest.fixture
def no_service(fastapi_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The app as started with RAG Lite off: no service yet; files go to a temporary folder."""
    had = getattr(fastapi_app.state, "rag_lite_service", None)
    if had is not None:
        del fastapi_app.state.rag_lite_service
    monkeypatch.setattr(settings, "rag_lite_file_store_root", str(tmp_path))
    yield
    if had is not None:
        fastapi_app.state.rag_lite_service = had
    elif getattr(fastapi_app.state, "rag_lite_service", None) is not None:
        del fastapi_app.state.rag_lite_service


def _file() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("note.txt", b"The plant room key is at the site office.", "text/plain")}


@pytest.mark.anyio
@pytest.mark.usefixtures("admin", "no_service")
async def test_an_upload_while_off_is_refused_with_what_to_do(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    answer = await client.post("/api/admin/rag/upload", files=_file())
    assert answer.status_code == status.HTTP_409_CONFLICT, answer.text
    assert answer.json()["detail"] == OFF


@pytest.mark.anyio
@pytest.mark.usefixtures("admin", "no_service")
async def test_switched_on_after_the_start_it_takes_an_upload_without_a_restart(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str]] = []

    async def kiq(document_id: str, job_id: str) -> None:
        sent.append((document_id, job_id))

    monkeypatch.setattr(settings, "rag_lite_enabled", True)
    monkeypatch.setattr(rag_lite_ingest_task, "kiq", kiq)
    answer = await client.post("/api/admin/rag/upload", files=_file())
    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert isinstance(fastapi_app.state.rag_lite_service, RagLiteService)
    assert sent == [(answer.json()["document_id"], answer.json()["job_id"])]


@pytest.mark.anyio
@pytest.mark.usefixtures("admin", "no_service")
async def test_a_retry_while_off_is_refused(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    answer = await client.post(f"/api/admin/rag/documents/{uuid.uuid4()}/retry")
    assert answer.status_code == status.HTTP_409_CONFLICT, answer.text
    assert answer.json()["detail"] == OFF


@pytest.mark.anyio
@pytest.mark.usefixtures("admin", "no_service")
async def test_nothing_is_searchable_while_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The gateway offers agents the knowledge tools only when this says so: while off, a search would be refused.
    async def anything(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(RagLiteChunkDAO, "any_searchable", anything)  # documents the reader could read
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    answer = await client.get("/api/admin/rag/searchable")
    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert answer.json() == {"searchable": False}


@pytest.mark.anyio
@pytest.mark.usefixtures("admin", "no_service")
async def test_deleting_a_document_works_while_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    answer = await client.delete(f"/api/admin/rag/documents/{uuid.uuid4()}")
    assert answer.status_code == status.HTTP_404_NOT_FOUND, answer.text


def test_the_service_is_made_once_and_only_while_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_lite_file_store_root", str(tmp_path))
    app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    assert service_for(app) is None
    assert not hasattr(app.state, "rag_lite_service")
    kept = service_for(app, even_off=True)
    assert isinstance(kept, RagLiteService)
    monkeypatch.setattr(settings, "rag_lite_enabled", True)
    assert service_for(app) is kept
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    assert service_for(app) is None
