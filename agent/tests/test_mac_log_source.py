"""On a Mac the system-log source is the agent's own log file, followed by (inode, offset)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from faden_agent.log_collector import LogCollector
from faden_agent.log_cursors import LogCursors


def _rec(n: int, level: str = "INFO", logger: str = "faden_agent.x", msg: str = "hello") -> str:
    return f"2026-11-28 00:03:{n % 60:02d},388 {level} [{logger}] {msg} {n}\n"


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(sys, "platform", "darwin")
    path = tmp_path / "agent.log"
    path.write_text("")

    def make(max_lines: int = 500) -> LogCollector:
        return LogCollector(max_lines=max_lines, log_file=path)

    return path, make


def _append(path: Path, text: str) -> None:
    with path.open("a") as fh:
        fh.write(text)


def test_follows_the_file_and_parses_level_and_time(mac) -> None:
    path, make = mac
    c = make()
    _, cur = c._collect_sync(None)
    _append(path, _rec(1) + _rec(2, "ERROR") + _rec(3, "WARNING") + _rec(4, "DEBUG"))
    entries, cur = c._collect_sync(cur)
    assert [e.level for e in entries] == ["info", "error", "warning", "debug"]
    assert entries[0].line == "2026-11-28 00:03:01,388 INFO [faden_agent.x] hello 1"
    assert entries[0].timestamp_ns % 1_000_000_000 == 388_000_000
    again, cur2 = c._collect_sync(cur)
    assert again == [] and cur2 == cur


def test_a_traceback_is_one_entry(mac) -> None:
    path, make = mac
    c = make()
    _append(path, _rec(1, "ERROR", msg="boom") + "Traceback (most recent call last):\n  File x\nValueError: bad\n" + _rec(2))
    entries, _ = c._collect_sync(None)
    assert len(entries) == 2
    assert entries[0].level == "error" and entries[0].line.endswith("ValueError: bad")
    assert entries[0].line.count("\n") == 3


def test_a_batch_is_capped_on_an_entry_boundary_and_continues(mac) -> None:
    path, make = mac
    c = make(max_lines=3)
    _append(path, "".join(_rec(i) for i in range(7)))
    got, cur = [], None
    for _ in range(5):
        entries, cur = c._collect_sync(cur)
        assert len(entries) <= 3
        got += [e.line for e in entries]
    assert len(got) == 7 and got[-1].endswith("hello 6")


def test_cursor_resumes_after_restart(mac, tmp_path: Path) -> None:
    path, make = mac
    _append(path, _rec(1) + _rec(2))
    cursors = LogCursors(tmp_path / "cur.json")
    entries, cur = make()._collect_sync(None)
    cursors.set("journal", cur)
    cursors.save()
    _append(path, _rec(3))
    restored = LogCursors(tmp_path / "cur.json").get("journal")
    entries, _ = make()._collect_sync(restored)
    assert [e.line[-1] for e in entries] == ["3"]


def test_rotation_reads_the_rest_of_the_old_file_then_the_new_one(mac) -> None:
    path, make = mac
    c = make()
    _append(path, _rec(1))
    _, cur = c._collect_sync(None)
    _append(path, _rec(2) + _rec(3))
    os.rename(path, path.with_name("agent.log.0"))
    path.write_text(_rec(4) + _rec(5))
    entries, cur = c._collect_sync(cur)
    assert [e.line[-1] for e in entries] == ["2", "3", "4", "5"]
    _append(path, _rec(6))
    entries, _ = c._collect_sync(cur)
    assert [e.line[-1] for e in entries] == ["6"]


def test_rotation_with_the_old_file_gone_starts_the_new_one_at_zero(mac) -> None:
    path, make = mac
    c = make()
    _append(path, _rec(1))
    _, cur = c._collect_sync(None)
    path.unlink()
    path.write_text(_rec(2))
    entries, _ = c._collect_sync(cur)
    assert [e.line[-1] for e in entries] == ["2"]


def test_a_shrunk_file_is_read_from_the_start(mac) -> None:
    path, make = mac
    c = make()
    _append(path, _rec(1) + _rec(2))
    _, cur = c._collect_sync(None)
    path.write_text(_rec(3))  # same inode, smaller
    entries, _ = c._collect_sync(cur)
    assert [e.line[-1] for e in entries] == ["3"]


def test_the_shippers_own_records_are_not_shipped(mac) -> None:
    path, make = mac
    _append(path, _rec(1, logger="httpx", msg="HTTP Request: POST") + _rec(2))
    entries, _ = make()._collect_sync(None)
    assert [e.line[-1] for e in entries] == ["2"]


def test_a_half_written_line_waits(mac) -> None:
    path, make = mac
    c = make()
    _append(path, _rec(1) + "2026-11-28 00:03:02,388 INFO [x] partial")
    entries, cur = c._collect_sync(None)
    assert len(entries) == 1
    _append(path, "\n")
    entries, _ = c._collect_sync(cur)
    assert entries[0].line.endswith("partial")


def test_linux_still_uses_journald(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    c = LogCollector()
    monkeypatch.setattr(c, "_collect_journald", lambda after: ([], "j"))
    monkeypatch.setattr(c, "_collect_file", lambda after: pytest.fail("file source on linux"))
    assert c._collect_sync(None) == ([], "j")
