"""Which commands run beside which (command_order.py).

Changes run one at a time and in order, as every command used to. Status
probes run together but never during a change -- a status taken halfway
through starting a cluster reads "down", and the server re-forms clusters it
finds down. Probes of the network run any time; transfers beside everything,
a few at a time.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from faden_agent.command_order import CHANGE, CHECK, OBSERVE, TRANSFER, CommandOrder, ReadWriteLock, kind_of


class _Trace:
    def __init__(self, order: CommandOrder) -> None:
        self.order = order
        self.events: list[tuple[str, str]] = []
        self.inside: set[str] = set()
        self.overlaps: list[frozenset[str]] = []

    async def run(self, command_type: str, name: str, seconds: float) -> None:
        async with self.order.turn(command_type):
            self.inside.add(name)
            self.overlaps.append(frozenset(self.inside))
            self.events.append(("in", name))
            await asyncio.sleep(seconds)
            self.inside.discard(name)
            self.events.append(("out", name))


def test_the_kinds() -> None:
    assert kind_of("sync_model") == TRANSFER
    assert kind_of("serve_runtime_image") == TRANSFER
    assert kind_of("get_ray_status") == OBSERVE
    assert kind_of("check_internet") == CHECK
    assert kind_of("start_ray_head") == CHANGE
    assert kind_of("ensure_runtime_image") == CHANGE, "it can make the runtime container anew"
    assert kind_of("a_command_from_a_later_server") == CHANGE, "unknown is treated as the least safe"


@pytest.mark.asyncio()
async def test_changes_run_one_at_a_time_in_the_order_they_came() -> None:
    trace = _Trace(CommandOrder())
    await asyncio.gather(trace.run("start_ray_head", "a", 0.02), trace.run("run_serve_app", "b", 0.0),
                         trace.run("stop_ray", "c", 0.0))
    assert trace.events == [("in", "a"), ("out", "a"), ("in", "b"), ("out", "b"), ("in", "c"), ("out", "c")]


@pytest.mark.asyncio()
async def test_a_status_waits_for_the_change_under_way_and_the_changes_wait_for_the_statuses() -> None:
    trace = _Trace(CommandOrder())
    await asyncio.gather(
        trace.run("get_ray_status", "s1", 0.02), trace.run("get_ray_serve_status", "s2", 0.02),
        trace.run("start_ray_head", "start", 0.02), trace.run("get_ray_status", "s3", 0.0),
    )
    assert frozenset({"s1", "s2"}) in trace.overlaps, "statuses together"
    assert all(not ({"start"} < group) for group in trace.overlaps), "never beside a change"
    assert trace.events.index(("out", "start")) < trace.events.index(("in", "s3")), "and in the order asked"


@pytest.mark.asyncio()
async def test_a_copy_runs_beside_a_change_and_a_status_beside_a_copy() -> None:
    trace = _Trace(CommandOrder())
    await asyncio.gather(trace.run("sync_model", "copy", 0.05), trace.run("start_ray_head", "start", 0.02),
                         trace.run("check_internet", "net", 0.0))
    assert frozenset({"copy", "start"}) in trace.overlaps or frozenset({"copy", "start", "net"}) in trace.overlaps


@pytest.mark.asyncio()
async def test_at_most_two_transfers() -> None:
    trace = _Trace(CommandOrder(max_transfers=2))
    await asyncio.gather(*(trace.run("sync_model", f"t{i}", 0.02) for i in range(3)))
    assert max(len(group) for group in trace.overlaps) == 2


@pytest.mark.asyncio()
async def test_a_command_waiting_its_turn_says_so() -> None:
    order = CommandOrder(waiting_notice_sec=0.01)
    notices: list[dict[str, Any]] = []

    async def notify(progress: dict[str, Any]) -> None:
        notices.append(progress)

    async def change(seconds: float) -> None:
        async with order.turn("start_ray_head"):
            await asyncio.sleep(seconds)

    async def waiting() -> None:
        async with order.turn("run_serve_app", notify=notify):
            pass

    await asyncio.gather(change(0.06), waiting())
    assert notices and notices[0]["phase"] == "queued"


@pytest.mark.asyncio()
async def test_a_waiting_writer_holds_back_readers_that_came_after_it() -> None:
    lock = ReadWriteLock()
    events: list[str] = []

    async def reader(name: str, hold: float) -> None:
        async with lock.read():
            events.append(f"{name} in")
            await asyncio.sleep(hold)
            events.append(f"{name} out")

    async def writer() -> None:
        await asyncio.sleep(0.005)
        async with lock.write():
            events.append("w in")
            events.append("w out")

    async def late_reader() -> None:
        await asyncio.sleep(0.01)
        await reader("r2", 0)

    await asyncio.gather(reader("r1", 0.03), writer(), late_reader())
    assert events.index("w in") < events.index("r2 in"), "a stream of probes cannot keep a change waiting"
