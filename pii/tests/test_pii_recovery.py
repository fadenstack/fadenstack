"""A failed native owner can recover once, never alongside another forward."""

import asyncio
import threading

import pytest

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.runtime import InferenceRuntime
from faden_pii.services.pii.windowing import Window
from faden_pii.settings import Settings

OPTIONS = {"language": "en", "entities": ["PERSON"], "score_threshold": 0.35}


class Detector:
    profile = Profile(backend="gliner2")

    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def prepare(self, text, options):
        assert not self.closed
        return [], [Window(0, text, 1)]

    def predict(self, pieces, options):
        if self.fail:
            raise MemoryError("simulated out of memory")
        return [[] for piece in pieces]

    def close(self):
        self.closed = True


@pytest.mark.anyio
async def test_native_failure_fails_closed_then_reloads_once():
    first = Detector(fail=True)
    second = Detector()
    runtime = InferenceRuntime(first, Settings(pii_recovery_delay_seconds=0))
    created = []

    def factory():
        assert first.closed
        created.append(second)
        return second

    runtime.detector_factory = factory
    try:
        with pytest.raises(PIIError, match="pii_unavailable"):
            await runtime.analyze(["first"], OPTIONS)
        await runtime._recovery_task
        assert runtime.healthy and runtime.stats["recoveries_completed"] == 1
        assert await runtime.analyze(["second"], OPTIONS) == [[]]
        second.fail = True
        with pytest.raises(PIIError, match="pii_unavailable"):
            await runtime.analyze(["third"], OPTIONS)
        assert not runtime.healthy
        assert runtime.stats["recoveries"] == 1
        assert created == [second]
        assert (
            runtime.retained_bytes
            == runtime.pending_tokens
            == runtime.pending_windows
            == 0
        )
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_shutdown_waits_for_native_loader_and_releases_replacement():
    first = Detector(fail=True)
    second = Detector()
    gate = threading.Event()
    entered = threading.Event()
    runtime = InferenceRuntime(first, Settings(pii_recovery_delay_seconds=0))

    def factory():
        entered.set()
        assert gate.wait(3)
        return second

    runtime.detector_factory = factory
    try:
        with pytest.raises(PIIError):
            await runtime.analyze(["first"], OPTIONS)
        while not entered.is_set():
            await asyncio.sleep(0.01)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.03)
        assert not closing.done()
        gate.set()
        await closing
        assert second.closed
        assert not runtime.healthy
    finally:
        gate.set()
        if not runtime.closed:
            await runtime.close()
