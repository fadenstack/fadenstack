"""A request's time grows with its windows (0.7.2): a base, an allowance per window, a cap."""

import asyncio
import time

import pytest

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.limits import IngressLimits
from faden_pii.services.pii.profiles import Profile
from faden_pii.services.pii.runtime import InferenceRuntime
from faden_pii.services.pii.runtime import RequestBudget
from faden_pii.services.pii.runtime import request_budget
from faden_pii.services.pii.windowing import Window
from faden_pii.settings import Settings

OPTIONS = {"language": "en", "entities": ["PERSON"], "score_threshold": 0.35}


def test_budget_grows_with_its_windows_up_to_its_limit():
    budget = RequestBudget(108.0, started=100.0, limit=130.0)
    budget.windows = 10
    budget.grow(8.0, 0.5)
    assert budget.deadline == 113.0
    budget.windows = 100
    budget.grow(8.0, 0.5)
    assert budget.deadline == 130.0


def test_budget_without_a_start_keeps_its_deadline():
    budget = RequestBudget(108.0)
    budget.windows = 50
    budget.grow(8.0, 0.5)
    assert budget.deadline == 108.0


def test_budget_never_moves_earlier():
    budget = RequestBudget(120.0, started=100.0, limit=130.0)
    budget.windows = 1
    budget.grow(8.0, 0.5)
    assert budget.deadline == 120.0


@pytest.mark.parametrize(
    ("values", "valid"),
    [
        ({}, True),
        ({"pii_work_seconds_per_window": 5.0}, True),
        ({"pii_work_seconds_per_window": 5.5}, False),
        ({"pii_work_seconds_per_window": -0.1}, False),
        ({"pii_work_timeout_cap_seconds": 60.0}, True),
        ({"pii_work_timeout_cap_seconds": 61.0}, False),
        ({"pii_work_timeout_seconds": 8.0, "pii_work_timeout_cap_seconds": 7.0}, False),
    ],
)
def test_the_settings_bound_the_allowance_and_the_cap(values, valid):
    if valid:
        Settings(**values)
    else:
        with pytest.raises(ValueError):
            Settings(**values)


class SlowDetector:
    """Six windows per text; each window takes *seconds* of inference."""

    profile = Profile(backend="gliner2")

    def __init__(self, seconds):
        self.seconds = seconds

    def prepare(self, text, options):
        return [], [Window(i, f"{text} {i}", 1) for i in range(6)]

    def predict(self, pieces, options):
        time.sleep(self.seconds * len(pieces))
        return [[] for _ in pieces]

    def close(self):
        pass


def settings(base, per_window, cap):
    return Settings(
        pii_work_timeout_seconds=base,
        pii_work_seconds_per_window=per_window,
        pii_work_timeout_cap_seconds=cap,
    )


@pytest.mark.anyio
async def test_a_long_text_finishes_after_the_base_time():
    # 6 windows at 0.1 s: 0.6 s of work, longer than the 0.3 s base, within 0.3 + 6 x 0.2.
    runtime = InferenceRuntime(SlowDetector(0.1), settings(0.3, 0.2, 5.0))
    try:
        started = time.monotonic()
        assert await runtime.analyze(["a long text"], OPTIONS) == [[]]
        assert time.monotonic() - started > 0.3
        assert runtime.stats["deadlines"] == 0
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_the_cap_still_ends_a_request():
    # 6 windows at 0.3 s: 1.8 s of work; the allowance would give 12.3 s, the cap 0.8 s.
    runtime = InferenceRuntime(SlowDetector(0.3), settings(0.3, 2.0, 0.8))
    try:
        started = time.monotonic()
        with pytest.raises(PIIError, match="pii_deadline_exceeded") as refused:
            await runtime.analyze(["a long text"], OPTIONS)
        assert refused.value.status == 504
        assert time.monotonic() - started < 1.5
        assert runtime.stats["deadlines"] == 1
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_the_callers_budget_ends_a_request_before_the_cap():
    # The caller allows 0.5 s (its x-pii-budget-ms): the request stops there, not at the 5 s cap.
    runtime = InferenceRuntime(SlowDetector(0.3), settings(0.3, 2.0, 5.0))
    now = time.monotonic()
    token = request_budget.set(RequestBudget(now + 0.3, started=now, limit=now + 0.5))
    try:
        with pytest.raises(PIIError, match="pii_deadline_exceeded"):
            await runtime.analyze(["a long text"], OPTIONS)
        assert time.monotonic() - now < 1.5
    finally:
        request_budget.reset(token)
        await runtime.close()


@pytest.mark.anyio
async def test_a_shared_job_does_not_stretch_another_requests_limit():
    # The first request (the service's own cap, 5 s) and a second with the same text and a 0.5 s budget share one
    # job, whose deadline grows for the first: the second still stops at its own 0.5 s.
    runtime = InferenceRuntime(SlowDetector(0.3), settings(0.3, 2.0, 5.0))

    async def limited():
        now = time.monotonic()
        request_budget.set(RequestBudget(now + 0.3, started=now, limit=now + 0.5))
        with pytest.raises(PIIError, match="pii_deadline_exceeded"):
            await runtime.analyze(["the same text"], OPTIONS)
        return time.monotonic() - now

    try:
        first = asyncio.create_task(runtime.analyze(["the same text"], OPTIONS))
        await asyncio.sleep(0.05)
        assert await asyncio.create_task(limited()) < 1.2
        assert runtime.stats["singleflight_hits"] == 1
        assert await first == [[]]
    finally:
        await runtime.close()


async def budget_for(header):
    """The budget IngressLimits gives a request with this x-pii-budget-ms (None: no header), from now."""
    seen = {}

    async def app(scope, receive, send):
        budget = request_budget.get()
        now = time.monotonic()
        seen["deadline"], seen["limit"] = budget.deadline - now, budget.limit - now

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        seen.setdefault("status", message.get("status"))

    headers = [] if header is None else [(b"x-pii-budget-ms", header)]
    await IngressLimits(app, Settings())({"type": "http", "method": "POST", "headers": headers}, receive, send)
    return seen


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("header", "deadline", "limit"),
    [
        (b"30000", 8, 30),  # the gateway's budget: the base now, up to 30 s as the windows come
        (b"60000", 8, 30),  # more than the cap: the cap
        (b"5000", 5, 5),  # less than the base: the caller's
        (None, 8, 8),  # no header (the console's test call): 8 s as before
    ],
)
async def test_ingress_takes_the_callers_budget_up_to_the_cap(header, deadline, limit):
    seen = await budget_for(header)
    assert "status" not in seen
    assert seen["deadline"] == pytest.approx(deadline, abs=0.5)
    assert seen["limit"] == pytest.approx(limit, abs=0.5)


@pytest.mark.anyio
@pytest.mark.parametrize("header", [b"nan", b"inf", b"-1", b"0", b"soon"])
async def test_ingress_refuses_a_budget_that_is_not_a_time(header):
    seen = await budget_for(header)
    assert seen.get("status") == 422
