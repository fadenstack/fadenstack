"""Getting a model onto the Fadenstack server as part of deploying it.

A cluster's runtime cannot reach the internet, so a model has to be on this
server before it can be copied to a machine. The onboarding guide made that a
separate step and called skipping it "the most common way a deployment
stalls": the deployment was accepted, sat on "Copying the model", and nothing
was being copied because there was nothing to copy.

The deployment knows which model it wants. So when the server does not have
it, start the download here and wait for it, instead of waiting for someone to
notice the ordering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.llm import DownloadJob, DownloadJobStatus, LLMModel, ModelStatus
from faden_backend.services.llm.artifacts import model_cache_dir
from faden_backend.settings import settings

log = logging.getLogger(__name__)

_ACTIVE = (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING)


@dataclass(frozen=True)
class ServerCopy:
    """What the deployment should do about a model the server lacks."""

    #: Set while a download is under way: wait, and show this.
    waiting: str | None = None
    #: Set when it cannot be fetched from here: this is the reason.
    blocker: str | None = None


async def _latest_job(session: AsyncSession, model_id) -> DownloadJob | None:
    result = await session.execute(
        select(DownloadJob)
        .where(DownloadJob.model_id == model_id)
        .order_by(DownloadJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _waiting_message(model: LLMModel, progress: int | None) -> str:
    done = f" ({progress}%)" if progress else ""
    return (
        f"Downloading {model.hf_repo_id} to the Fadenstack server{done}. "
        f"It is copied to the cluster as soon as it is there."
    )


async def download_under_way(session: AsyncSession, model: LLMModel) -> str | None:
    """What to show while this server is still downloading *model*, else None.

    The model's folder holds files by then, the partial ones named
    ``<hash>.incomplete``, so it must not be read as a copy to send.
    """
    job = await _latest_job(session, model.id)
    if job is not None and job.status in _ACTIVE:
        return _waiting_message(model, job.progress)
    return None


async def incomplete_server_copy(session: AsyncSession, model: LLMModel) -> str | None:
    """Why this server's copy of *model* must not be sent to the machines yet, else None.

    A download that stopped leaves the files that arrived, and a folder that
    looks like a copy. Sending it had both machines of the release VM's
    cluster fail on the missing shards, twice, and the deployment with them.
    Complete means: the model's last download finished (or the model was put
    here some other way and is available), and no file is still partial.
    """
    if not model.hf_repo_id:
        return None
    job = await _latest_job(session, model.id)
    if job is not None and job.status != DownloadJobStatus.SUCCESS and model.status != ModelStatus.AVAILABLE:
        how = {
            DownloadJobStatus.CANCELED: "was canceled",
            DownloadJobStatus.FAILED: "stopped" if job.error_code else "failed",
        }.get(job.status, "has not finished")
        reason = f": {job.error_message}" if job.error_message else ""
        return (
            f"The server's copy of {model.hf_repo_id} is incomplete: its download {how}{reason}. "
            f"Start the download again from Models; it resumes where it stopped."
        )
    folder = model_cache_dir(model.hf_repo_id)
    blobs = folder / "blobs" if folder is not None else None
    partial = sorted(blobs.glob("*.incomplete")) if blobs is not None and blobs.is_dir() else []
    if partial:
        return (
            f"The server's copy of {model.hf_repo_id} is incomplete: {len(partial)} file(s) "
            f"did not finish downloading. Start the download again from Models."
        )
    return None


async def _resume(session: AsyncSession, model: LLMModel, job: DownloadJob) -> ServerCopy:
    """Start an interrupted download again; it picks up the files already on disk."""
    from faden_backend.db.dao.llm_dao import DownloadJobDAO  # noqa: PLC0415
    from faden_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

    await DownloadJobDAO(session).requeue(job.id)
    model.status = ModelStatus.DOWNLOADING
    await session.flush()
    try:
        await download_model_task.kiq(
            model_id=str(model.id),
            job_id=str(job.id),
            hf_repo_id=model.hf_repo_id,
            hf_revision=model.hf_revision,
            target_dir=settings.model_store_root,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        log.warning("Could not resume the download of %s: %s", model.hf_repo_id, exc)
        return ServerCopy(blocker=f"Could not resume downloading {model.hf_repo_id}: {exc}")
    log.info("Resumed the interrupted server-side download of %s for a deployment", model.hf_repo_id)
    done = f" ({job.progress}%)" if job.progress else ""
    return ServerCopy(
        waiting=(
            f"Resuming the download of {model.hf_repo_id} to the Fadenstack server{done}. "
            f"It is copied to the cluster as soon as it is there."
        )
    )


async def ensure_server_copy(session: AsyncSession, model: LLMModel, *, resume_only: bool = False) -> ServerCopy:
    """Start or follow the server-side download of *model*.

    Never starts a second download of the same model, and never restarts one
    that failed: a failure is usually a missing token or a wrong repository
    name, and retrying it on every reconcile pass would be the same storm of
    identical attempts the cluster reconciler used to produce. The operator
    retries from Models once it is fixed, and the deployment picks it up.

    An *interrupted* download is another matter: nothing was wrong with it,
    its worker went away (``services/llm/downloads.py``). It is resumed.
    *resume_only* does only that, and otherwise returns an empty answer.
    """
    if not model.hf_repo_id:
        return ServerCopy(
            blocker=(
                f"{model.display_name} has no local copy on this server and no "
                f"repository to download it from. Import it on the server first."
            )
        )

    job = await _latest_job(session, model.id)
    if job is not None and job.status in _ACTIVE:
        return ServerCopy(waiting=_waiting_message(model, job.progress))

    from faden_backend.db.dao.llm_dao import INTERRUPTED  # noqa: PLC0415

    if job is not None and job.status == DownloadJobStatus.FAILED and job.error_code == INTERRUPTED:
        return await _resume(session, model, job)
    if resume_only:
        return ServerCopy()

    if job is not None and job.status == DownloadJobStatus.FAILED:
        reason = job.error_message or "no reason recorded"
        return ServerCopy(
            blocker=(
                f"Downloading {model.hf_repo_id} to the Fadenstack server failed: "
                f"{reason}. Retry it from Models; the deployment carries on once "
                f"the model is there."
            )
        )

    # Nothing under way, and nothing failed: start it.
    from faden_backend.db.dao.llm_dao import DownloadJobDAO  # noqa: PLC0415
    from faden_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

    new_job = await DownloadJobDAO(session).create(model.id)
    model.status = ModelStatus.DOWNLOADING
    await session.flush()
    try:
        await download_model_task.kiq(
            model_id=str(model.id),
            job_id=str(new_job.id),
            hf_repo_id=model.hf_repo_id,
            hf_revision=model.hf_revision,
            target_dir=settings.model_store_root,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        new_job.status = DownloadJobStatus.FAILED
        new_job.error_message = f"Could not start the download: {exc}"
        log.warning("Could not dispatch download of %s: %s", model.hf_repo_id, exc)
        return ServerCopy(blocker=f"Could not start downloading {model.hf_repo_id}: {exc}")

    log.info("Started server-side download of %s for a deployment", model.hf_repo_id)
    return ServerCopy(waiting=_waiting_message(model, 0))
