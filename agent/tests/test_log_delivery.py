"""No log line is dropped on its way to Loki: the log on disk is the queue.

Each shipper moved its cursor as soon as it had read, whether Loki took the
lines or not, so every failed push -- every restart of the server -- lost
them. The journal reader also asked for the *last* N entries after its
cursor, skipping the middle of anything longer than one batch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from faden_agent import log_collector
from faden_agent.container_log_forwarder import ContainerLogForwarder
from faden_agent.log_collector import LogCollector
from faden_agent.log_cursors import LogCursors

pytestmark = pytest.mark.anyio()


# ── the journal ──────────────────────────────────────────────────────────


class _Journalctl:
    """Stands in for ``journalctl --output=json``: the entries after a cursor, oldest first."""

    calls: list[list[str]] = []

    def __init__(self, cmd: list[str], **_: Any) -> None:
        type(self).calls.append(cmd)
        after = cmd[cmd.index("--after-cursor") + 1] if "--after-cursor" in cmd else None
        start = int(after.removeprefix("c")) + 1 if after else 0
        self.stdout = iter(
            json.dumps({"__CURSOR": f"c{i}", "__REALTIME_TIMESTAMP": str(1_790_000_000_000_000 + i),
                        "MESSAGE": f"entry {i}", "PRIORITY": "6", "SYSLOG_IDENTIFIER": "faden-agent",
                        "_PID": "42", "_HOSTNAME": "gpu-01"}) + "\n"
            for i in range(start, 25)
        )

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        pass

    def wait(self) -> int:
        return 0


def test_the_journal_is_read_forward_from_the_cursor_one_batch_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(log_collector.sys, "platform", "linux")
    monkeypatch.setattr("subprocess.Popen", _Journalctl)
    _Journalctl.calls = []
    collector = LogCollector(max_lines=10)

    seen: list[str] = []
    position = "c-1"  # a cursor before the first entry
    for _ in range(4):
        entries, position = collector._collect_sync(position)
        seen += [entry.line.split(": ", 1)[1] for entry in entries]

    assert seen == [f"entry {i}" for i in range(25)], "nothing skipped, nothing twice"
    assert all("--lines=10" not in call for call in _Journalctl.calls)


def test_a_journal_entry_keeps_its_time_its_source_and_its_severity() -> None:
    entry = LogCollector._journal_entry({
        "__REALTIME_TIMESTAMP": "1790749780000000", "MESSAGE": list(b"bytes \xff message"),
        "PRIORITY": "3", "SYSLOG_IDENTIFIER": "faden-agent", "_PID": "7", "_HOSTNAME": "gpu-02",
    })
    assert entry.timestamp_ns == 1_790_749_780_000_000_000
    assert entry.level == "error"
    assert entry.line.endswith("gpu-02 faden-agent[7]: bytes � message")


# ── container logs ───────────────────────────────────────────────────────


class _Runtime:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.since: list[str | None] = []

    async def logs(self, name: str, *, tail: str | None, since: str | None, timestamps: bool) -> tuple[int, str]:
        self.since.append(since)
        kept = [line for line in self.lines if since is None or line.split(" ", 1)[0] >= since]
        return 0, "\n".join(kept)


class _Loki:
    def __init__(self) -> None:
        self.down = False
        self.sent: list[str] = []

    async def push_streams(self, *, labels: dict[str, str], entries: list) -> bool:
        if self.down:
            return False
        self.sent += [text for _, text in entries]
        return True


class _State:
    class state:  # noqa: N801 - mirrors StateStore.state
        workloads = {"rt-1": {"container_name": "faden-rt-1"}}


def _line(i: int) -> str:
    return f"2026-09-30T06:{i // 60:02d}:{i % 60:02d}.000000000Z line {i}"


async def test_container_lines_loki_refused_are_sent_again(tmp_path: Path) -> None:
    runtime, loki = _Runtime([_line(0), _line(1)]), _Loki()
    cursors = LogCursors(tmp_path / "log-cursors.json")
    forwarder = ContainerLogForwarder(runtime=runtime, state_store=_State(), loki=loki, host="h", cursors=cursors)

    loki.down = True
    await forwarder._collect_all()
    assert forwarder._cursors == {}

    loki.down = False
    await forwarder._collect_all()
    assert loki.sent == ["line 0", "line 1"]
    # Kept for the next agent: a restart resumes here.
    assert LogCursors(tmp_path / "log-cursors.json").get("container:rt-1") == _line(1).split(" ")[0]


async def test_a_container_backlog_drains_over_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_agent import container_log_forwarder

    monkeypatch.setattr(container_log_forwarder, "_MAX_LINES_PER_PASS", 4)
    runtime, loki = _Runtime([_line(i) for i in range(10)]), _Loki()
    forwarder = ContainerLogForwarder(runtime=runtime, state_store=_State(), loki=loki, host="h")
    for _ in range(4):
        await forwarder._collect_all()
    # ``--since`` includes the line it names, which Loki takes as the same entry again.
    assert sorted(set(loki.sent), key=lambda t: int(t.split()[1])) == [f"line {i}" for i in range(10)]
