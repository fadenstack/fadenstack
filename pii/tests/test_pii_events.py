"""Slow metadata delivery must not occupy inference or unbounded memory."""

import asyncio

import pytest

from faden_pii.services.pii.events import EventDispatcher
from faden_pii.settings import Settings


@pytest.mark.anyio
async def test_metadata_queue_is_bounded_and_backend_refusals_are_counted():
    gate = asyncio.Event()
    seen = []

    async def send(**event):
        seen.append(event)
        await gate.wait()
        return False

    reporter = EventDispatcher(send, Settings(pii_event_entries=1))
    reporter.submit(
        operation="scan", source="unbounded-user-label", request_id="x" * 300
    )
    await asyncio.sleep(0)
    reporter.submit(operation="scan")
    reporter.submit(operation="scan")
    assert reporter.dropped == 1
    assert reporter.bytes > 0
    gate.set()
    await reporter.close()
    assert reporter.bytes == 0
    assert reporter.failed == 2
    assert seen[0]["source"] == "api"
    assert len(seen[0]["request_id"]) == 128
