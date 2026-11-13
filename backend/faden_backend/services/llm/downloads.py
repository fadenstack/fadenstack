"""Downloads no process is doing any more, told apart from the ones that are.

A download runs in the task worker and says every ``HEARTBEAT_SEC`` that it
is alive (``services/llm/tasks.py``). One whose heartbeat stopped is a
download whose worker died or was stopped: it is marked interrupted, and
starting it again resumes it from what is on disk.

This replaced marking *every* running download failed whenever the web
backend started. The web backend is not where downloads run, and a web
process is restarted on its own -- its supervisor replaces a child that stops
answering, as on a server short of memory. On the release VM that marked a
132 GB download failed twice while the worker went on downloading it, and a
deployment waiting for it then sent the machines the half that had arrived.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.llm_dao import DownloadJobDAO
from faden_backend.db.models.llm import DownloadJob, DownloadJobStatus, LLMModel, ModelStatus
from faden_backend.services.llm.tasks import HEARTBEAT_SEC

log = logging.getLogger(__name__)

#: A running download silent this long is taken for interrupted: thirty
#: missed heartbeats, far past a busy host's pause.
STALE_AFTER_SEC = 30 * HEARTBEAT_SEC
#: A queued download no worker took up in this long lost its task message.
QUEUED_AFTER_SEC = 1800.0

_ACTIVE = (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING)


async def cancel_download(session: AsyncSession, job_id: uuid.UUID) -> DownloadJob | None:
    """Cancel a download; its model is failed now, unless another download of it is under way.

    Canceling only marks the job. A running download hears of it before its
    next file and fails the model itself -- but a queued one is never started,
    and a running one whose worker died never hears of it: their models stayed
    "downloading" until the backend's next start. Should the files all have
    arrived before the download hears of it, it marks the model available
    after all, which is what is on disk.
    """
    dao = DownloadJobDAO(session)
    job = await dao.set_canceled(job_id)
    if job is None:
        return None
    model = await session.get(LLMModel, job.model_id)
    # The query flushes the cancel first, so this job is not counted as under way.
    if (
        model is not None
        and model.status == ModelStatus.DOWNLOADING
        and await dao.active_for_model(model.id) is None
    ):
        model.status = ModelStatus.FAILED
    await session.flush()
    return job


async def interrupt_stale_downloads(session: AsyncSession, *, orphaned_models: bool = False) -> int:
    """Mark downloads no process is doing as interrupted; how many there were.

    Their models are marked failed unless another download of them is under
    way. *orphaned_models* also fails any model left downloading with no
    download under way at all -- what a crash before heartbeats left behind.
    """
    model_ids = await DownloadJobDAO(session).interrupt_stale(
        stale_after_sec=STALE_AFTER_SEC, queued_after_sec=QUEUED_AFTER_SEC,
    )
    active = select(DownloadJob.model_id).where(DownloadJob.status.in_(_ACTIVE))
    fixed = 0
    if model_ids or orphaned_models:
        stmt = (
            update(LLMModel)
            .where(LLMModel.status == ModelStatus.DOWNLOADING, LLMModel.id.not_in(active))
            .values(status=ModelStatus.FAILED)
            .execution_options(synchronize_session=False)
        )
        if not orphaned_models:
            stmt = stmt.where(LLMModel.id.in_(model_ids))
        fixed = (await session.execute(stmt)).rowcount or 0
    if model_ids or fixed:
        log.warning(
            "Marked %d download(s) interrupted (no heartbeat for %.0f s) and %d model(s) failed; "
            "starting them again resumes them.",
            len(model_ids), STALE_AFTER_SEC, fixed,
        )
    return len(model_ids)
