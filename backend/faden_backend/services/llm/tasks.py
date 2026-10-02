"""Taskiq background tasks for LLM model downloads."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from faden_backend.tkq import broker

log = logging.getLogger(__name__)

#: How often the size of the file being downloaded is read for progress.
_PROGRESS_POLL_SEC = 2.0

#: How often a running download says it is alive (``download_jobs.heartbeat_at``).
HEARTBEAT_SEC = 20.0
#: How long a running download may say nothing before another delivery of its
#: task takes it over. Several heartbeats: a busy host can miss one or two.
TAKEOVER_AFTER_SEC = 120.0
#: Attempts at a download that the network broke off, before it is failed.
_NETWORK_ATTEMPTS = 3
_NETWORK_BACKOFF_SEC = 15.0


class DownloadCanceled(Exception):
    """The download job was canceled while it ran."""


class _Run:
    """One run of the download task: whether it still holds the job.

    RabbitMQ hands a task message out again when the worker's channel closes
    -- after 30 minutes without an acknowledgement by default, and a large
    model downloads for hours -- and when a worker is killed. The second
    delivery used to start a second download of the same model next to the
    first, in the same worker, doubling its memory until the kernel killed it.
    """

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        #: Set when this run must stop: the job was canceled, or is another run's now.
        self.stop = threading.Event()
        #: Another run holds the job: leave the job and the model as they are.
        self.lost = False


#: Weights in a format vLLM loads, at the top of a repository.
_WEIGHTS = (".safetensors", ".bin", ".pt", ".pth")
#: The same weights exported for other runtimes.
_EXPORTS = (".onnx", ".onnx_data", ".gguf", ".h5", ".msgpack", ".ot", ".tflite", ".mlmodel")
#: Folders of such exports, of a second copy for another runtime, or of a training run.
_OTHER_FOLDERS = frozenset({"onnx", "openvino", "coreml", "gguf", "tflite", "metal", "original", "runs"})
#: What a training run leaves beside the model.
_TRAINING_STATE = ("training_args.bin", "optimizer.", "scheduler.", "rng_state", "events.out.tfevents")


def files_to_fetch(names: list[str]) -> list[str]:
    """The files of a repository that a copy on this server is for: what vLLM reads.

    Repositories carry much else. HuggingFaceTB/SmolLM2-360M-Instruct is a
    724 MB model in 4.4 GB of files: eight ONNX exports, TensorBoard runs and
    the training state. All of it was downloaded, then copied to every
    machine, over the release VM's 100 Mbit/s link.

    Only a layout this knows is filtered: weights vLLM loads at the top of
    the repository. Anything else (a GGUF-only repository, say) is fetched
    whole, as before.
    """
    def training_state(name: str) -> bool:
        return name.rsplit("/", 1)[-1].startswith(_TRAINING_STATE)

    top_weights = [n for n in names if "/" not in n and n.endswith(_WEIGHTS) and not training_state(n)]
    if not top_weights:
        return list(names)
    safetensors = any(n.endswith(".safetensors") for n in top_weights)
    keep = []
    for name in names:
        folder = name.split("/", 1)[0] if "/" in name else ""
        if folder in _OTHER_FOLDERS or folder.startswith("checkpoint-"):
            continue
        if name.lower().endswith(_EXPORTS) or training_state(name):
            continue
        if safetensors and name.endswith((".bin", ".pt", ".pth")):
            continue  # the same weights again, in PyTorch's format
        keep.append(name)
    return keep


async def _resolve_hf_token() -> str | None:
    """The token this server uses: the stored one, else ``FADEN_BACKEND_HF_TOKEN``, else anonymous."""
    from faden_backend.services.llm import hf_token  # noqa: PLC0415

    app = broker.state.fastapi_app
    session = app.state.db_session_factory()
    try:
        token, _source = await hf_token.resolve(session)
        return token
    finally:
        await session.close()


def _run_download_sync(
    hf_repo_id: str,
    hf_revision: str,
    target_dir: str,
    hf_token: str | None,
    progress_callback: object,
    cancel_event: threading.Event | None = None,
) -> str:
    """Run the HF download in a thread -- calls *progress_callback(pct)* as bytes arrive.

    Downloads into the standard HuggingFace hub cache structure under
    *target_dir* (used as ``cache_dir``).  Returns the absolute path to
    the snapshot directory that contains the model files.

    Progress follows bytes, not files: a model is mostly one or a few large
    weight files, and counting files left it on one number for the whole of
    each. While a file downloads, the size of its ``.incomplete`` blob in the
    cache is read every couple of seconds. *cancel_event* is checked with each
    reading and before each file; a file already under way finishes first
    (the Hub client has no way to stop one), which with sharded weights is
    at most one shard.
    """
    from pathlib import Path  # noqa: PLC0415

    from huggingface_hub import HfApi, hf_hub_download  # noqa: PLC0415

    Path(target_dir).mkdir(parents=True, exist_ok=True)

    api = HfApi(token=hf_token)
    repo_info = api.model_info(hf_repo_id, revision=hf_revision, files_metadata=True)

    listed = repo_info.siblings or []
    wanted = set(files_to_fetch([s.rfilename for s in listed]))
    siblings = [s for s in listed if s.rfilename in wanted]
    total_files = len(siblings)
    if not listed:
        # Fall back to snapshot_download if we can't enumerate files
        from huggingface_hub import snapshot_download  # noqa: PLC0415

        snapshot_dir = snapshot_download(
            repo_id=hf_repo_id,
            revision=hf_revision,
            cache_dir=target_dir,
            token=hf_token,
        )
        progress_callback(85)  # type: ignore[operator]
        return snapshot_dir

    log.info(
        "Downloading %d files for %s (rev %s); %d others are not for vLLM",
        total_files,
        hf_repo_id,
        hf_revision,
        len(listed) - total_files,
    )

    total_bytes = sum(int(getattr(s, "size", 0) or 0) for s in siblings)
    blobs = Path(target_dir) / f"models--{hf_repo_id.replace('/', '--')}" / "blobs"
    done_bytes = 0
    done_files = 0

    def _report(partial: int = 0) -> None:
        # 0-85% is downloading, 85-90% is scanning, 90-100% is finalising
        if total_bytes > 0:
            pct = int(min(done_bytes + partial, total_bytes) / total_bytes * 85)
        else:
            pct = int(done_files / total_files * 85)
        progress_callback(pct)  # type: ignore[operator]

    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(_PROGRESS_POLL_SEC):
            try:
                partial = sum(p.stat().st_size for p in blobs.glob("*.incomplete")) if blobs.is_dir() else 0
            except OSError:
                partial = 0
            _report(partial)

    watcher = threading.Thread(target=_watch, name=f"download-progress-{hf_repo_id}", daemon=True)
    watcher.start()
    try:
        for sibling in siblings:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCanceled(hf_repo_id)
            hf_hub_download(
                repo_id=hf_repo_id,
                filename=sibling.rfilename,
                revision=hf_revision,
                cache_dir=target_dir,
                token=hf_token,
            )
            done_bytes += int(getattr(sibling, "size", 0) or 0)
            done_files += 1
            _report()
    finally:
        stop.set()
        watcher.join(timeout=_PROGRESS_POLL_SEC * 2)

    # Derive the snapshot directory from the HF cache structure:
    # {cache_dir}/models--{org}--{model}/snapshots/{commit_hash}/
    commit_hash = repo_info.sha
    model_cache_name = f"models--{hf_repo_id.replace('/', '--')}"
    snapshot_dir = str(Path(target_dir) / model_cache_name / "snapshots" / commit_hash)
    return snapshot_dir


async def _claim(app: object, job_id: uuid.UUID, model_id: uuid.UUID, run: _Run) -> str | None:
    """Take the job for *run*: None once it holds it, else why this run should not download.

    A job another run is downloading is left to it: ``busy``. That run may
    also have died a moment ago -- a killed worker's message comes back at
    once, with the dead run's heartbeat still fresh -- so a heartbeat that
    stays where it was is waited out until the job can be taken over.
    """
    from faden_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from faden_backend.db.models.llm import DownloadJobStatus, ModelStatus  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    deadline = loop.time() + TAKEOVER_AFTER_SEC + 2 * HEARTBEAT_SEC
    first_beat: object = None
    while True:
        session = app.state.db_session_factory()  # type: ignore[attr-defined]
        try:
            dao = DownloadJobDAO(session)
            if await dao.claim(job_id, run.id, stale_after_sec=TAKEOVER_AFTER_SEC):
                await ModelDAO(session).set_status(model_id, ModelStatus.DOWNLOADING)
                await session.commit()
                return None
            job = await dao.get(job_id)
            status, beat = (job.status, job.heartbeat_at) if job is not None else (None, None)
            await session.rollback()
        finally:
            await session.close()
        if status is None:
            return "gone"
        if status not in (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING):
            return str(status.value)
        if first_beat is None:
            first_beat = beat
        elif beat != first_beat:
            return "busy"  # its heartbeat moves: another run is downloading it
        if loop.time() >= deadline:
            return "busy"
        await asyncio.sleep(HEARTBEAT_SEC / 2)


async def _heartbeat(app: object, job_id: uuid.UUID, model_id: uuid.UUID, run: _Run) -> None:
    """Say every ``HEARTBEAT_SEC`` that *run* is downloading; stop it when the job is not its own any more."""
    from faden_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from faden_backend.db.models.llm import ModelStatus  # noqa: PLC0415

    while not run.stop.is_set():
        await asyncio.sleep(HEARTBEAT_SEC)
        session = app.state.db_session_factory()  # type: ignore[attr-defined]
        try:
            state = await DownloadJobDAO(session).heartbeat(job_id, run.id)
            if state == "resumed":
                # Taken for interrupted while this host was too busy to say
                # otherwise; it is downloading, so say that again.
                await ModelDAO(session).set_status(model_id, ModelStatus.DOWNLOADING)
            await session.commit()
        except Exception:  # noqa: BLE001 - a missed heartbeat is not a failed download
            log.warning("Heartbeat of download job %s failed (non-fatal)", job_id, exc_info=True)
            continue
        finally:
            await session.close()
        if state in ("running", "resumed"):
            continue
        if state != "canceled":
            run.lost = True
            log.info("Download job %s is not this run's any more (%s); stopping after the current file", job_id, state)
        run.stop.set()


@broker.task(retry_on_error=False)
async def download_model_task(
    model_id: str,
    job_id: str,
    hf_repo_id: str,
    hf_revision: str | None,
    target_dir: str,
) -> dict:
    """
    Download a model from Hugging Face Hub and register its artifacts.

    The HF token is resolved at runtime from the encrypted system settings
    (or env-var fallback) -- it is never transmitted over RabbitMQ.

    Safe to deliver twice: a run first takes the job (``_claim``), and a
    second delivery of the message finds it taken and returns. A run that
    died is taken over, and the download resumes from what is on disk.
    """
    from faden_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from faden_backend.db.models.llm import ModelStatus  # noqa: PLC0415

    _model_id = uuid.UUID(model_id)
    _job_id = uuid.UUID(job_id)

    app = broker.state.fastapi_app
    run = _Run()
    not_mine = await _claim(app, _job_id, _model_id, run)
    if not_mine is not None:
        log.info("Not downloading %s for job %s: %s", hf_repo_id, _job_id, not_mine)
        return {"status": "skipped", "reason": not_mine}

    hf_token = await _resolve_hf_token()
    session = app.state.db_session_factory()
    beat = asyncio.create_task(_heartbeat(app, _job_id, _model_id, run), name=f"download-heartbeat-{_job_id}")

    try:
        return await _do_download(
            session=session,
            model_id=_model_id,
            job_id=_job_id,
            hf_repo_id=hf_repo_id,
            hf_revision=hf_revision,
            target_dir=target_dir,
            hf_token=hf_token,
            run=run,
        )
    except Exception as exc:
        if run.lost:
            log.info("Download run for %s ended after another run took the job: %s", hf_repo_id, exc)
            return {"status": "superseded"}
        log.exception("Download task crashed for %s: %s", hf_repo_id, exc)
        # Best-effort status update in a *fresh* session so we don't reuse a
        # potentially broken one.
        err_session = app.state.db_session_factory()
        try:
            err_model_dao = ModelDAO(err_session)
            err_job_dao = DownloadJobDAO(err_session)
            await err_model_dao.set_status(_model_id, ModelStatus.FAILED)
            await err_job_dao.set_failed(_job_id, str(exc))
            await err_session.commit()
        except Exception:
            log.exception("Could not update status after crash")
        finally:
            await err_session.close()
        return {"status": "failed", "error": str(exc)}
    finally:
        run.stop.set()
        beat.cancel()
        await session.close()


def _network_error(exc: BaseException) -> bool:
    """Whether *exc* is the network breaking off, which trying again can get past.

    A wrong token or repository name is not: trying again only repeats it.
    """
    markers = ("connection", "timed out", "timeout", "reset by peer", "broken pipe", "incomplete", "chunked")
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        module = type(current).__module__ or ""
        name = type(current).__name__.lower()
        if module.startswith(("requests", "urllib3", "httpx", "httpcore", "http.client")) and any(
            m in name for m in ("connection", "timeout", "chunked", "protocol", "readerror", "incomplete", "remotedisconnected")
        ):
            return True
        if module.startswith("hf_xet") or "xet" in name:
            return any(m in str(current).lower() for m in markers)
        current = current.__cause__ or current.__context__
    return False


async def _do_download(
    *,
    session: object,
    model_id: uuid.UUID,
    job_id: uuid.UUID,
    hf_repo_id: str,
    hf_revision: str | None,
    target_dir: str,
    hf_token: str | None,
    run: _Run | None = None,
) -> dict:
    """Inner download logic — any exception propagates to caller."""
    from faden_backend.db.dao.llm_dao import ArtifactDAO, DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from faden_backend.db.models.llm import DownloadJobStatus, ModelStatus  # noqa: PLC0415
    from faden_backend.services.llm.scanner import scan_model_directory  # noqa: PLC0415

    model_dao = ModelDAO(session)  # type: ignore[arg-type]
    job_dao = DownloadJobDAO(session)  # type: ignore[arg-type]
    artifact_dao = ArtifactDAO(session)  # type: ignore[arg-type]

    _last_pct = 0
    _loop = asyncio.get_running_loop()
    canceled = run.stop if run is not None else threading.Event()

    async def _flush_progress(pct: int) -> None:
        nonlocal _last_pct
        # Canceling only marks the job: this is where the download hears of it.
        if await job_dao.status_of(job_id) == DownloadJobStatus.CANCELED:
            canceled.set()
            return
        if pct <= _last_pct:
            return
        _last_pct = pct
        await job_dao.update_progress(job_id, pct, DownloadJobStatus.RUNNING)
        await session.commit()  # type: ignore[union-attr]

    def _sync_progress(pct: int) -> None:
        """Thread-safe bridge: schedule the async DB update on the event loop."""
        try:
            asyncio.run_coroutine_threadsafe(_flush_progress(pct), _loop).result(timeout=10)
        except Exception:
            log.warning("Progress update to %d%% failed (non-fatal)", pct)

    # Mark job as running
    await job_dao.update_progress(job_id, 0, DownloadJobStatus.RUNNING)
    await session.commit()  # type: ignore[union-attr]

    log.info("Starting HF download: %s → %s", hf_repo_id, target_dir)

    revision = hf_revision or "main"

    # Run the blocking HF download in a thread with progress callbacks.
    # target_dir is used as the HF hub cache_dir; the function returns
    # the snapshot directory containing the actual model files.
    attempt = 0
    while True:
        attempt += 1
        try:
            snapshot_dir = await asyncio.to_thread(
                _run_download_sync,
                hf_repo_id,
                revision,
                target_dir,
                hf_token,
                _sync_progress,
                canceled,
            )
            break
        except DownloadCanceled:
            if run is not None and run.lost:
                # Another run holds the job and downloads the model: leave both alone.
                log.info("Stopped downloading %s: another run holds the job", hf_repo_id)
                return {"status": "superseded"}
            # The job already says canceled; the model has no complete copy.
            await model_dao.set_status(model_id, ModelStatus.FAILED)
            await session.commit()  # type: ignore[union-attr]
            log.info("Download canceled: %s", hf_repo_id)
            return {"status": "canceled"}
        except Exception as exc:
            # A download of hours is broken off by the network now and then.
            # What arrived stays on disk, so trying again picks up there.
            if attempt >= _NETWORK_ATTEMPTS or canceled.is_set() or not _network_error(exc):
                raise
            wait = _NETWORK_BACKOFF_SEC * attempt
            log.warning(
                "Downloading %s broke off (%s); trying again in %.0f s (attempt %d of %d)",
                hf_repo_id, exc, wait, attempt + 1, _NETWORK_ATTEMPTS,
            )
            await asyncio.sleep(wait)

    if run is not None and run.lost:
        log.info("Downloaded %s, but another run holds the job now; it records the result", hf_repo_id)
        return {"status": "superseded"}

    # 90%: scanning artifacts
    await _flush_progress(90)

    # Scan downloaded files in the snapshot directory
    artifacts = await asyncio.to_thread(scan_model_directory, snapshot_dir)
    if artifacts:
        await artifact_dao.create_batch(model_id, artifacts)

    # Mark model available and job success
    await model_dao.set_status(model_id, ModelStatus.AVAILABLE)
    await job_dao.update_progress(job_id, 100, DownloadJobStatus.SUCCESS)
    await session.commit()  # type: ignore[union-attr]

    log.info("Download complete: %s (%d artifacts)", hf_repo_id, len(artifacts))
    return {"status": "success", "artifacts": len(artifacts)}
