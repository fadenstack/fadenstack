"""A download job knows which run holds it and whether that run is alive.

On the release VM a 132 GB download was marked failed twice while its worker
went on downloading: a web process restarted, and its startup failed every
running download. A deployment waiting for the model then sent both machines
the half that had arrived. RabbitMQ also handed the task out again every 30
minutes, and each delivery started another download of the same model.

Against Postgres: the claim, the heartbeat and the sweep are single UPDATEs
whose WHERE clauses are the whole point.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.llm_dao import INTERRUPTED, DownloadJobDAO
from faden_backend.db.models.llm import DownloadJob, DownloadJobStatus, LLMModel, ModelSource, ModelStatus
from faden_backend.services.inference import server_copy
from faden_backend.services.llm import tasks as llm_tasks
from faden_backend.services.llm.downloads import STALE_AFTER_SEC, interrupt_stale_downloads

REPO = "nvidia/Qwen3.8-Flash-Next-NVFP4"


async def _model(session: AsyncSession, status: ModelStatus = ModelStatus.DOWNLOADING) -> LLMModel:
    model = LLMModel(
        id=uuid.uuid4(), display_name="Qwen3.8-Flash-Next-NVFP4", source=ModelSource.HUGGINGFACE,
        hf_repo_id=f"{REPO}-{uuid.uuid4().hex[:6]}", status=status,
    )
    session.add(model)
    await session.flush()
    return model


async def _job(session: AsyncSession, model: LLMModel, **fields: Any) -> DownloadJob:
    job = DownloadJob(**({"id": uuid.uuid4(), "model_id": model.id, "status": DownloadJobStatus.QUEUED, "progress": 0} | fields))
    session.add(job)
    await session.flush()
    return job


async def _age(session: AsyncSession, job: DownloadJob, seconds: float, *, column: str = "heartbeat_at") -> None:
    await session.execute(
        update(DownloadJob).where(DownloadJob.id == job.id)
        .values({column: datetime.now(tz=UTC) - timedelta(seconds=seconds)})
        .execution_options(synchronize_session=False)
    )


async def _read(session: AsyncSession, job: DownloadJob) -> DownloadJob:
    await session.refresh(job)
    return job


# -- the claim ---------------------------------------------------------------


async def test_a_queued_job_is_taken_by_the_first_run_only(dbsession: AsyncSession) -> None:
    dao = DownloadJobDAO(dbsession)
    job = await _job(dbsession, await _model(dbsession))

    assert await dao.claim(job.id, "first", stale_after_sec=120) is True
    # RabbitMQ delivers the same message again: the first run is alive.
    assert await dao.claim(job.id, "second", stale_after_sec=120) is False

    job = await _read(dbsession, job)
    assert (job.status, job.runner_id) == (DownloadJobStatus.RUNNING, "first")
    assert job.heartbeat_at is not None


async def test_a_run_that_stopped_saying_so_is_taken_over(dbsession: AsyncSession) -> None:
    """The worker was killed: its heartbeat stops, and the message comes back."""
    dao = DownloadJobDAO(dbsession)
    job = await _job(dbsession, await _model(dbsession))
    await dao.claim(job.id, "killed", stale_after_sec=120)
    await _age(dbsession, job, 121)

    assert await dao.claim(job.id, "next", stale_after_sec=120) is True
    assert (await _read(dbsession, job)).runner_id == "next"


async def test_a_job_from_before_heartbeats_can_be_taken(dbsession: AsyncSession) -> None:
    """The release VM's running job, after the upgrade: no heartbeat at all, and no worker."""
    dao = DownloadJobDAO(dbsession)
    job = await _job(dbsession, await _model(dbsession), heartbeat_at=None)
    await dbsession.execute(
        update(DownloadJob).where(DownloadJob.id == job.id).values(status=DownloadJobStatus.RUNNING)
        .execution_options(synchronize_session=False)
    )
    assert await dao.claim(job.id, "after-upgrade", stale_after_sec=120) is True


@pytest.mark.parametrize(
    ("status", "code", "free"),
    [
        (DownloadJobStatus.FAILED, INTERRUPTED, True),
        (DownloadJobStatus.FAILED, None, False),
        (DownloadJobStatus.CANCELED, None, False),
        (DownloadJobStatus.SUCCESS, None, False),
    ],
    ids=["interrupted resumes", "failed stays failed", "canceled stays canceled", "done stays done"],
)
async def test_what_a_run_may_take(dbsession: AsyncSession, status: DownloadJobStatus, code: str | None, free: bool) -> None:
    job = await _job(dbsession, await _model(dbsession), status=status, error_code=code)
    assert await DownloadJobDAO(dbsession).claim(job.id, "run", stale_after_sec=120) is free


# -- the heartbeat ------------------------------------------------------------


async def test_the_heartbeat_tells_a_run_whether_to_go_on(dbsession: AsyncSession) -> None:
    dao = DownloadJobDAO(dbsession)
    job = await _job(dbsession, await _model(dbsession))
    await dao.claim(job.id, "mine", stale_after_sec=120)

    assert await dao.heartbeat(job.id, "mine") == "running"
    assert await dao.heartbeat(job.id, "someone-else") == "taken"

    await dao.set_canceled(job.id)
    await dbsession.flush()
    assert await dao.heartbeat(job.id, "mine") == "canceled"


async def test_a_run_taken_for_interrupted_while_alive_carries_on(dbsession: AsyncSession) -> None:
    """A host too busy to say so for ten minutes: the sweep gave up on it, but it is downloading."""
    dao = DownloadJobDAO(dbsession)
    job = await _job(dbsession, await _model(dbsession))
    await dao.claim(job.id, "mine", stale_after_sec=120)
    await dbsession.execute(
        update(DownloadJob).where(DownloadJob.id == job.id)
        .values(status=DownloadJobStatus.FAILED, error_code=INTERRUPTED)
        .execution_options(synchronize_session=False)
    )

    assert await dao.heartbeat(job.id, "mine") == "resumed"
    job = await _read(dbsession, job)
    assert (job.status, job.error_code) == (DownloadJobStatus.RUNNING, None)


# -- the sweep ----------------------------------------------------------------


async def test_a_live_download_survives_the_sweep_and_a_dead_one_is_interrupted(dbsession: AsyncSession) -> None:
    dao = DownloadJobDAO(dbsession)
    live_model, dead_model = await _model(dbsession), await _model(dbsession)
    live = await _job(dbsession, live_model)
    dead = await _job(dbsession, dead_model)
    await dao.claim(live.id, "live", stale_after_sec=120)
    await dao.claim(dead.id, "dead", stale_after_sec=120)
    await _age(dbsession, live, 30)  # a heartbeat or two ago
    await _age(dbsession, dead, STALE_AFTER_SEC + 1)

    assert await interrupt_stale_downloads(dbsession) == 1

    live, dead = await _read(dbsession, live), await _read(dbsession, dead)
    assert live.status == DownloadJobStatus.RUNNING
    assert (dead.status, dead.error_code) == (DownloadJobStatus.FAILED, INTERRUPTED)
    await dbsession.refresh(live_model)
    await dbsession.refresh(dead_model)
    assert live_model.status == ModelStatus.DOWNLOADING
    assert dead_model.status == ModelStatus.FAILED


async def test_a_web_restart_leaves_a_running_download_alone(dbsession: AsyncSession) -> None:
    """What the startup recovery did on the VM at 11:44 and 12:07: failed a download the worker was doing."""
    dao = DownloadJobDAO(dbsession)
    model = await _model(dbsession)
    job = await _job(dbsession, model)
    await dao.claim(job.id, "worker", stale_after_sec=120)

    await interrupt_stale_downloads(dbsession, orphaned_models=True)  # what startup runs

    assert (await _read(dbsession, job)).status == DownloadJobStatus.RUNNING
    await dbsession.refresh(model)
    assert model.status == ModelStatus.DOWNLOADING


async def test_a_queued_job_whose_message_was_lost_is_interrupted(dbsession: AsyncSession) -> None:
    job = await _job(dbsession, await _model(dbsession))
    await _age(dbsession, job, 3600, column="updated_at")
    assert await interrupt_stale_downloads(dbsession) == 1
    assert (await _read(dbsession, job)).error_code == INTERRUPTED


# -- what a deployment may send ----------------------------------------------


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from faden_backend.settings import settings

    monkeypatch.setattr(settings, "model_store_root", str(tmp_path))
    return tmp_path


def _files(store: Path, model: LLMModel, *, partial: bool) -> None:
    blobs = store / f"models--{model.hf_repo_id.replace('/', '--')}" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "63fde954").write_bytes(b"x" * 10)  # the shard that finished
    if partial:
        (blobs / "4dafaef6.incomplete").write_bytes(b"x" * 3)


async def test_a_stopped_download_is_not_a_copy_to_send(dbsession: AsyncSession, store: Path) -> None:
    model = await _model(dbsession, ModelStatus.FAILED)
    await _job(dbsession, model, status=DownloadJobStatus.FAILED, error_code=INTERRUPTED,
               error_message="The download stopped")
    _files(store, model, partial=False)

    reason = await server_copy.incomplete_server_copy(dbsession, model)
    assert reason is not None and "incomplete" in reason and model.hf_repo_id in reason


async def test_a_partial_file_is_not_a_copy_to_send(dbsession: AsyncSession, store: Path) -> None:
    model = await _model(dbsession, ModelStatus.AVAILABLE)
    await _job(dbsession, model, status=DownloadJobStatus.SUCCESS)
    _files(store, model, partial=True)
    assert "did not finish" in (await server_copy.incomplete_server_copy(dbsession, model) or "")


async def test_a_finished_download_is_a_copy_to_send(dbsession: AsyncSession, store: Path) -> None:
    model = await _model(dbsession, ModelStatus.AVAILABLE)
    await _job(dbsession, model, status=DownloadJobStatus.SUCCESS)
    _files(store, model, partial=False)
    assert await server_copy.incomplete_server_copy(dbsession, model) is None


async def test_a_deployment_resumes_an_interrupted_download(
    dbsession: AsyncSession, store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []

    async def kiq(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(llm_tasks.download_model_task, "kiq", kiq)
    model = await _model(dbsession, ModelStatus.FAILED)
    job = await _job(dbsession, model, status=DownloadJobStatus.FAILED, error_code=INTERRUPTED, progress=41)

    copy = await server_copy.ensure_server_copy(dbsession, model)

    assert copy.waiting is not None and "Resuming" in copy.waiting and "41%" in copy.waiting
    assert [s["job_id"] for s in sent] == [str(job.id)], "the same job, so it picks up its files"
    job = await _read(dbsession, job)
    assert (job.status, job.error_code) == (DownloadJobStatus.QUEUED, None)
    assert model.status == ModelStatus.DOWNLOADING


async def test_a_failed_download_is_not_resumed_by_itself(
    dbsession: AsyncSession, store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong token or repository fails the same way again: the operator retries it."""
    sent: list[dict] = []

    async def kiq(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(llm_tasks.download_model_task, "kiq", kiq)
    model = await _model(dbsession, ModelStatus.FAILED)
    await _job(dbsession, model, status=DownloadJobStatus.FAILED, error_message="401 Unauthorized")

    copy = await server_copy.ensure_server_copy(dbsession, model)
    assert copy.blocker is not None and "401" in copy.blocker
    assert sent == []


# -- a job made in a request that has not committed yet ------------------------


class _Session:
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def close(self) -> None: ...


class _App:
    class state:  # noqa: N801 - shaped like FastAPI's app.state
        @staticmethod
        def db_session_factory() -> _Session:
            return _Session()


def _jobs_seen_after(monkeypatch: pytest.MonkeyPatch, looks: int | None) -> dict[str, int]:
    """DAOs over a job the task sees only from its *looks*-th look on (never, for None)."""
    from faden_backend.db.dao import llm_dao

    seen = {"looks": 0}

    class _Jobs:
        def __init__(self, _session: object) -> None: ...

        async def claim(self, *_args: object, **_kw: object) -> bool:
            seen["looks"] += 1
            return looks is not None and seen["looks"] >= looks

        async def get(self, _job_id: object) -> None:
            return None

    class _Models:
        def __init__(self, _session: object) -> None: ...

        async def set_status(self, *_args: object) -> None: ...

    monkeypatch.setattr(llm_dao, "DownloadJobDAO", _Jobs)
    monkeypatch.setattr(llm_dao, "ModelDAO", _Models)
    monkeypatch.setattr(llm_tasks, "_UNSEEN_POLL_SEC", 0.01)
    return seen


async def test_a_job_not_committed_yet_is_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task arrived 20 ms after the job was made, before the request committed (a fresh install)."""
    seen = _jobs_seen_after(monkeypatch, looks=4)
    assert await llm_tasks._claim(_App(), uuid.uuid4(), uuid.uuid4(), llm_tasks._Run()) is None
    assert seen["looks"] == 4


async def test_a_job_that_never_appears_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    _jobs_seen_after(monkeypatch, looks=None)
    monkeypatch.setattr(llm_tasks, "JOB_COMMIT_GRACE_SEC", 0.1)
    assert await llm_tasks._claim(_App(), uuid.uuid4(), uuid.uuid4(), llm_tasks._Run()) == "gone"
