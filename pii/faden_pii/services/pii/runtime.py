"""Bounded async admission, one native owner, cross-request window batches."""

import asyncio
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import time
from typing import Any

from presidio_analyzer import RecognizerResult

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.windowing import Window


@dataclass
class RequestBudget:
    deadline: float
    scope: str = ""
    windows: int = 0


request_budget: ContextVar[RequestBudget | None] = ContextVar(
    "pii_request_budget", default=None
)


class LegacyDetector:
    """Keep the injected batch seam, including existing analyzer tests."""

    def __init__(self, batch, analyzer):
        self.batch = batch
        engine = getattr(analyzer, "nlp_engine", None)
        self.profile = Profile(
            languages=tuple(engine.get_supported_languages()) if engine else ("en",)
        )

    def prepare(self, text, options):
        return [], [Window(0, text, max(1, len(text.split())))]

    def predict(self, pieces, options):
        return self.batch().analyze_iterator(
            texts=[p.text for p in pieces], batch_size=len(pieces), **options
        )

    def close(self):
        pass


@dataclass(eq=False)
class Job:
    key: tuple
    text: str
    options: dict
    budget: RequestBudget
    future: Any
    size: int
    subscribers: int = 1
    deadline: float = 0
    pieces: Any = field(default_factory=deque)
    results: list = field(default_factory=list)
    remaining_tokens: int = 0
    remaining_windows: int = 0
    prepared: bool = False
    finished: bool = False


class InferenceRuntime:
    """Queued bytes/futures remain charged until native work actually ends."""

    def __init__(self, detector, settings):
        self.detector, self.settings = detector, settings
        self.profile = detector.profile
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pii-inference"
        )
        self.jobs: dict[tuple, Job] = {}
        self.retained_bytes = self.pending_tokens = self.pending_windows = 0
        self.stats = Counter()
        self.healthy = True
        self.failure_type = None
        self.failure_frames = []
        self.closed = False
        self.detector_factory = self.on_reload = None
        self._recovery_task = None
        self._task = self._loop = self._queue = None

    async def analyze(self, texts, options):
        if not self.healthy or self.closed:
            raise PIIError("pii_unavailable", 503)
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            if self.jobs:
                raise PIIError("pii_unavailable", 503)
            self._loop = loop
            self._queue = asyncio.Queue(maxsize=self.settings.pii_pending_windows)
            self._task = loop.create_task(self._run())
        budget = request_budget.get() or RequestBudget(
            time.monotonic() + self.settings.pii_work_timeout_seconds
        )
        if budget.deadline <= time.monotonic():
            raise PIIError("pii_deadline_exceeded", 504)
        acquired = []
        try:
            for text in texts:
                key = (
                    hashlib.sha256(text.encode()).digest(),
                    self.profile.fingerprint,
                    budget.scope,
                    options["language"],
                    tuple(sorted(options["entities"])),
                    options["score_threshold"],
                )
                if key in self.jobs:
                    job = self.jobs[key]
                    if job.subscribers >= self.settings.pii_max_subscribers:
                        raise PIIError("pii_overloaded", 503)
                    job.subscribers += 1
                    job.deadline = max(job.deadline, budget.deadline)
                    self.stats["singleflight_hits"] += 1
                else:
                    size = len(text.encode()) + 512
                    if (
                        len(self.jobs) >= self.settings.pii_pending_windows
                        or self.retained_bytes + size > self.settings.pii_pending_bytes
                    ):
                        self.stats["rejected"] += 1
                        raise PIIError("pii_overloaded", 503)
                    job = Job(
                        key,
                        text,
                        options,
                        budget,
                        loop.create_future(),
                        size,
                        deadline=budget.deadline,
                    )
                    # Consume exceptions even if every subscriber disconnected.
                    job.future.add_done_callback(
                        lambda f: None if f.cancelled() else f.exception()
                    )
                    self.jobs[key] = job
                    self.retained_bytes += size
                    self._queue.put_nowait(job)
                acquired.append(job)
            async with asyncio.timeout_at(
                loop.time() + max(0, budget.deadline - time.monotonic())
            ):
                return await asyncio.gather(
                    *(asyncio.shield(j.future) for j in acquired)
                )
        except TimeoutError as error:
            self.stats["deadlines"] += 1
            raise PIIError("pii_deadline_exceeded", 504) from error
        finally:
            for job in acquired:
                job.subscribers -= 1
                if job.finished and not job.subscribers:
                    self._retire(job)

    def _retire(self, job):
        if self.jobs.pop(job.key, None) is not None:
            self.retained_bytes -= job.size

    def _finish(self, job, result=None, error=None):
        if job.finished:
            return
        job.finished = True
        self.pending_windows -= job.remaining_windows
        job.remaining_windows = 0
        self.pending_tokens -= job.remaining_tokens
        job.remaining_tokens = 0
        if not job.future.done():
            if error is None:
                job.future.set_result(result)
            else:
                job.future.set_exception(error)
        job.text = ""
        job.pieces.clear()
        if not job.subscribers:
            self._retire(job)

    async def _native(self, function, *args):
        future = self._loop.run_in_executor(self.executor, function, *args)
        # Do not release native capacity when a caller/owner task is canceled.
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await future
            raise

    async def _prepare(self, job):
        if not job.subscribers or job.deadline <= time.monotonic():
            self._finish(job, error=PIIError("pii_deadline_exceeded", 504))
            return False
        started = time.monotonic()
        rules, pieces = await self._native(self.detector.prepare, job.text, job.options)
        self.stats["preparation_seconds"] += time.monotonic() - started
        cost = sum(p.tokens for p in pieces)
        if job.budget.windows + len(pieces) > self.settings.pii_max_request_windows:
            raise PIIError("pii_input_too_large", 413)
        if (
            self.pending_tokens + cost > self.settings.pii_pending_tokens
            or self.pending_windows + len(pieces) > self.settings.pii_pending_windows
        ):
            raise PIIError("pii_overloaded", 503)
        job.budget.windows += len(pieces)
        job.results.extend(rules)
        job.pieces.extend(pieces)
        job.remaining_windows = len(pieces)
        self.pending_windows += len(pieces)
        job.remaining_tokens = cost
        self.pending_tokens += cost
        job.prepared = True
        return True

    async def _run(self):
        ready = deque()
        try:
            while not self.closed:
                if not ready:
                    ready.append(await self._queue.get())
                    await asyncio.sleep(self.settings.pii_batch_wait_ms / 1000)
                while (
                    not self._queue.empty()
                    and len(ready) < self.settings.pii_batch_short
                ):
                    ready.append(self._queue.get_nowait())
                batch, owners = [], []
                options = None
                batch_key = None
                for _ in range(len(ready)):
                    job = ready.popleft()
                    longest = max((p.tokens for p in batch), default=0)
                    cap = (
                        self.settings.pii_batch_short
                        if longest <= 128
                        else self.settings.pii_batch_long
                    )
                    if len(batch) >= cap:
                        ready.append(job)
                        continue
                    if not job.subscribers or job.deadline <= time.monotonic():
                        self._finish(job, error=PIIError("pii_deadline_exceeded", 504))
                        continue
                    key = (
                        self.detector.batch_key(job.options)
                        if hasattr(self.detector, "batch_key")
                        else job.options
                    )
                    if options is not None and key != batch_key:
                        ready.append(job)
                        continue
                    try:
                        if not job.prepared and not await self._prepare(job):
                            continue
                    except PIIError as error:
                        self._finish(job, error=error)
                        continue
                    options = job.options
                    batch_key = key
                    for _share in range(min(4, len(job.pieces))):
                        piece = job.pieces[0]
                        longest = max([p.tokens for p in batch] + [piece.tokens])
                        short = longest <= 128 or self.profile.backend == "presidio"
                        count_cap = (
                            self.settings.pii_batch_short
                            if short
                            else self.settings.pii_batch_long
                        )
                        token_cap = (
                            self.settings.pii_batch_short_tokens
                            if short
                            else self.settings.pii_batch_long_tokens
                        )
                        if batch and (
                            len(batch) + 1 > count_cap
                            or (len(batch) + 1) * longest > token_cap
                        ):
                            break
                        batch.append(job.pieces.popleft())
                        owners.append(job)
                    if job.pieces:
                        ready.append(job)
                if not batch:
                    continue
                started = time.monotonic()
                self.stats["active_batches"] = 1
                try:
                    rows = await self._native(self.detector.predict, batch, options)
                except PIIError as error:
                    # A malformed result refuses this batch; it must not poison
                    # every subsequent tenant request or masquerade as clean.
                    self.stats["invalid_result_batches"] += 1
                    self.stats["active_batches"] = 0
                    for job in set(owners):
                        self._finish(job, error=error)
                    ready = deque(job for job in ready if not job.finished)
                    continue
                self.stats["active_batches"] = 0
                self.stats["inference_seconds"] += time.monotonic() - started
                self.stats["batches"] += 1
                self.stats["windows"] += len(batch)
                self.stats["max_padded_tokens"] = max(
                    self.stats["max_padded_tokens"],
                    len(batch) * max(p.tokens for p in batch),
                )
                for piece, job, spans in zip(batch, owners, rows, strict=True):
                    job.results.extend(
                        RecognizerResult(
                            s.entity_type,
                            s.start + piece.offset,
                            s.end + piece.offset,
                            s.score,
                        )
                        for s in spans
                    )
                    job.remaining_windows -= 1
                    self.pending_windows -= 1
                    job.remaining_tokens -= piece.tokens
                    self.pending_tokens -= piece.tokens
                for job in set(owners):
                    if not job.pieces:
                        self._finish(job, job.results)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Includes native OOM/invalid output. No result is promoted as clean.
            self.healthy = False
            self.stats["owner_failures"] += 1
            import traceback

            self.failure_type = type(error).__name__
            self.failure_frames = [
                (frame.filename, frame.name, frame.lineno)
                for frame in traceback.extract_tb(error.__traceback__)
            ]
            if "out of memory" in str(error).lower() or isinstance(error, MemoryError):
                self.stats["ooms"] += 1
        finally:
            self.stats["active_batches"] = 0
            for job in list(self.jobs.values()):
                self._finish(job, error=PIIError("pii_unavailable", 503))
            if (
                not self.closed
                and not self.healthy
                and self.detector_factory is not None
                and self.stats["recoveries"] < self.settings.pii_max_recoveries
            ):
                self.stats["recoveries"] += 1
                self._recovery_task = self._loop.create_task(self._recover())

    def invalidate(self):
        """Stop admission and drain the actual native owner before reloading."""
        self.healthy = False
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _recover(self):
        replacement = None
        try:
            await asyncio.sleep(self.settings.pii_recovery_delay_seconds)
            if self.closed:
                return
            await self._native(self.detector.close)
            if self.closed:
                return
            replacement = await self._native(self.detector_factory)
            if (
                self.closed
                or replacement.profile.fingerprint != self.profile.fingerprint
            ):
                await self._native(replacement.close)
                replacement = None
                if not self.closed:
                    self.stats["recovery_failures"] += 1
                return
            self.detector = replacement
            self.profile = replacement.profile
            if self.on_reload is not None:
                self.on_reload(replacement)
            while not self._queue.empty():
                self._queue.get_nowait()
            self.stats["recoveries_completed"] += 1
            self.healthy = True
            self._task = self._loop.create_task(self._run())
        except Exception:
            self.stats["recovery_failures"] += 1
            if replacement is not None:
                await self._native(replacement.close)

    async def close(self):
        self.closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._recovery_task is not None:
            # A running native loader cannot be canceled safely. It observes
            # closed and releases the replacement before shutdown finishes.
            await asyncio.gather(self._recovery_task, return_exceptions=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.detector.close()
