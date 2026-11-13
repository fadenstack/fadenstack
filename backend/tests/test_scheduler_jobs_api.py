"""Canceling and retrying a download job from the scheduler answers with the job.

On 0.7.0 the scheduler's cancel answered 500 every time (seen on the test
server, 12 Nov 2026): after the job's status changed, the next query flushed
it, the flush gave ``updated_at`` its new value in the database only, and
reading it for the answer tried to load it again outside the async context
(``MissingGreenlet``). The request's transaction was rolled back with it, so
the download went on. Retrying broke the same way after its explicit flush.

A canceled download's model is failed at once, unless another download of it
is under way: a queued download is never started, so nothing else would.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.llm_dao import ModelDAO
from faden_backend.db.models.llm import DownloadJob, DownloadJobStatus, LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm import tasks as llm_tasks


@pytest.fixture
async def superuser(dbsession: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="scheduler-admin@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106 - never signed in with
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


@pytest.fixture
def signed_in(fastapi_app: FastAPI, superuser: User) -> User:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    return superuser


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def kiq(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(llm_tasks.download_model_task, "kiq", kiq)
    return sent


async def _model(dbsession: AsyncSession) -> LLMModel:
    model = await ModelDAO(session=dbsession).create(
        display_name="Qwen3.8-Flash",
        source=ModelSource.HUGGINGFACE,
        hf_repo_id="example/Qwen3.8-Flash",
        hf_revision="main",
        license_ack_required=False,
        tags=[],
        status=ModelStatus.DOWNLOADING,
    )
    await dbsession.refresh(model)
    return model


async def _job(dbsession: AsyncSession, model: LLMModel, job_status: DownloadJobStatus) -> DownloadJob:
    job = DownloadJob(id=uuid.uuid4(), model_id=model.id, status=job_status, progress=37)
    dbsession.add(job)
    await dbsession.flush()
    await dbsession.refresh(job)
    return job


async def _stored_status(dbsession: AsyncSession, job_id: uuid.UUID) -> DownloadJobStatus:
    return (await dbsession.execute(select(DownloadJob.status).where(DownloadJob.id == job_id))).scalar_one()


async def _model_status(dbsession: AsyncSession, model_id: uuid.UUID) -> ModelStatus:
    return (await dbsession.execute(select(LLMModel.status).where(LLMModel.id == model_id))).scalar_one()


@pytest.mark.parametrize("job_status", [DownloadJobStatus.RUNNING, DownloadJobStatus.QUEUED])
async def test_the_scheduler_cancels_a_download(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User, job_status: DownloadJobStatus,
) -> None:
    model = await _model(dbsession)
    job = await _job(dbsession, model, job_status)

    resp = await client.post(f"/api/admin/scheduler/jobs/{job.id}/cancel")

    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["id"] == str(job.id)
    assert body["status"] == "canceled"
    assert body["label"] == "Qwen3.8-Flash"
    assert body["updated_at"]
    assert await _stored_status(dbsession, job.id) == DownloadJobStatus.CANCELED
    assert await _model_status(dbsession, model.id) == ModelStatus.FAILED


async def test_the_jobs_api_cancels_a_queued_download_and_fails_its_model(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User,
) -> None:
    model = await _model(dbsession)
    job = await _job(dbsession, model, DownloadJobStatus.QUEUED)

    resp = await client.post(f"/api/llm/jobs/{job.id}/cancel")

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["status"] == DownloadJobStatus.CANCELED.value
    assert await _model_status(dbsession, model.id) == ModelStatus.FAILED


async def test_a_model_another_download_is_fetching_stays_downloading(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User,
) -> None:
    model = await _model(dbsession)
    job = await _job(dbsession, model, DownloadJobStatus.QUEUED)
    other = await _job(dbsession, model, DownloadJobStatus.RUNNING)

    resp = await client.post(f"/api/admin/scheduler/jobs/{job.id}/cancel")

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert await _stored_status(dbsession, other.id) == DownloadJobStatus.RUNNING
    assert await _model_status(dbsession, model.id) == ModelStatus.DOWNLOADING


async def test_the_scheduler_does_not_cancel_a_finished_download(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User,
) -> None:
    job = await _job(dbsession, await _model(dbsession), DownloadJobStatus.SUCCESS)

    resp = await client.post(f"/api/admin/scheduler/jobs/{job.id}/cancel")

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert await _stored_status(dbsession, job.id) == DownloadJobStatus.SUCCESS


async def test_the_scheduler_retries_a_canceled_download(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User, dispatched: list[dict[str, Any]],
) -> None:
    job = await _job(dbsession, await _model(dbsession), DownloadJobStatus.CANCELED)

    resp = await client.post(f"/api/admin/scheduler/jobs/{job.id}/retry")

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["status"] == "queued"
    assert [d["job_id"] for d in dispatched] == [str(job.id)]
    assert await _stored_status(dbsession, job.id) == DownloadJobStatus.QUEUED


async def test_the_jobs_api_retries_a_failed_download(
    client: AsyncClient, dbsession: AsyncSession, signed_in: User, dispatched: list[dict[str, Any]],
) -> None:
    job = await _job(dbsession, await _model(dbsession), DownloadJobStatus.FAILED)

    resp = await client.post(f"/api/llm/jobs/{job.id}/retry")

    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["status"] == DownloadJobStatus.QUEUED.value
    assert body["progress"] == 0
    assert body["updated_at"]
    assert [d["job_id"] for d in dispatched] == [str(job.id)]
