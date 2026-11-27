"""System log collection for Linux (journald), Windows (Event Log) and macOS (the agent's log file).

Yields timestamped log lines from the host OS, resuming from
the last-seen cursor on each call.  The collector is designed to
be called periodically from an async loop — heavy I/O runs in a
thread executor so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ``2026-11-28 00:03:20,388 INFO [logger] message`` -- the agent's own format.
_AGENT_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) (\w+) \[([^\]]*)\] ")
# Loggers whose records are the shipping itself; sending them would feed the loop.
_SELF_LOGGERS = ("httpx", "httpcore", "faden_agent.loki")
_FIRST_READ_BYTES = 256 * 1024


@dataclass(slots=True)
class LogEntry:
    """Single log line with nanosecond-precision timestamp."""

    timestamp_ns: int
    line: str
    level: str = "info"


class LogCollector:
    """Cross-platform system log reader.

    On Linux, reads from ``journalctl --output=short-iso`` (no extra
    dependencies).  On Windows, reads the System event log through
    ``wevtutil``.  Both approaches use cursor-based pagination so that
    only new entries are returned on each call.
    """

    def __init__(self, *, max_lines: int = 500, log_file: Path | None = None) -> None:
        self._max_lines = max_lines
        self._log_file = log_file

    async def collect(self, after: str | None = None) -> tuple[list[LogEntry], str | None]:
        """Up to ``max_lines`` entries after the position *after*, oldest first, and the new position.

        The position is only a proposal: the caller keeps it once the entries
        are in Loki, and otherwise asks again from *after* -- the journal (or
        the event log) is the queue. With no position yet, the newest entries.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._collect_sync, after)

    def _collect_sync(self, after: str | None) -> tuple[list[LogEntry], str | None]:
        if sys.platform == "win32":
            return self._collect_windows(after)
        if sys.platform == "darwin":
            return self._collect_file(after)
        return self._collect_journald(after)

    # ── Linux: journalctl ────────────────────────────────────

    def _collect_journald(self, after: str | None) -> tuple[list[LogEntry], str | None]:
        """Read forward from *after*, stopping at ``max_lines``.

        ``--lines`` with ``--after-cursor`` gives the *last* N entries after
        the cursor, which skipped the middle of anything longer than one
        batch. So entries are read as JSON -- each carries its own cursor --
        and the read stops at the budget.
        """
        import subprocess

        cmd = ["journalctl", "--no-pager", "--output=json"]
        cmd += ["--after-cursor", after] if after else [f"--lines={self._max_lines}"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except FileNotFoundError:
            log.debug("journalctl not found — system log collection disabled.")
            return [], after

        entries: list[LogEntry] = []
        position = after
        deadline = time.monotonic() + 10
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                try:
                    doc = json.loads(raw)
                except ValueError:
                    continue
                entries.append(self._journal_entry(doc))
                position = doc.get("__CURSOR") or position
                if len(entries) >= self._max_lines or time.monotonic() > deadline:
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        return entries, position

    @staticmethod
    def _journal_entry(doc: dict[str, Any]) -> LogEntry:
        """One journal entry as the short-iso line it used to be shipped as."""
        try:
            ts_ns = int(doc.get("__REALTIME_TIMESTAMP")) * 1000
        except (TypeError, ValueError):
            ts_ns = time.time_ns()
        message = doc.get("MESSAGE")
        if isinstance(message, list):  # binary: an array of bytes
            message = bytes(message).decode("utf-8", errors="replace")
        stamp = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).isoformat(timespec="seconds")
        ident = doc.get("SYSLOG_IDENTIFIER") or doc.get("_COMM") or "-"
        pid = doc.get("_PID")
        source = f"{ident}[{pid}]" if pid else str(ident)
        line = f"{stamp} {doc.get('_HOSTNAME', '-')} {source}: {message or ''}"
        try:
            priority = int(doc.get("PRIORITY", 6))
        except (TypeError, ValueError):
            priority = 6
        level = "error" if priority <= 3 else "warning" if priority == 4 else "debug" if priority >= 7 else "info"
        return LogEntry(timestamp_ns=ts_ns, line=line, level=level)

    @staticmethod
    def _parse_journal_line_meta(line: str) -> tuple[int, str]:
        """Extract timestamp and severity from a journalctl short-iso line.

        Format: ``2026-03-21T14:05:32+0000 hostname unit[pid]: message``
        """
        level = "info"
        ts_ns = time.time_ns()  # fallback

        # Try to parse the ISO timestamp at the start
        parts = line.split(" ", 1)
        if parts:
            try:
                dt = datetime.fromisoformat(parts[0])
                ts_ns = int(dt.timestamp() * 1_000_000_000)
            except ValueError:
                pass

        # Heuristic level detection from message content
        lower = line.lower()
        if "error" in lower or "fatal" in lower or "crit" in lower:
            level = "error"
        elif "warn" in lower:
            level = "warning"
        elif "debug" in lower:
            level = "debug"

        return ts_ns, level

    # ── macOS: the agent's own log file ──────────────────────

    # A Mac has no journal; the system-log source is the service's log file,
    # followed by (inode, offset) so newsyslog rotation loses nothing.
    # (Follow-up: the unified log via ``log show``.)

    def _collect_file(self, after: str | None) -> tuple[list[LogEntry], str | None]:
        path = self._log_file
        if path is None:
            from faden_agent.darwin_service import Layout  # noqa: PLC0415

            path = Layout().log_file
        try:
            return self._follow(path, after)
        except OSError as exc:
            log.debug("Cannot read %s: %s", path, exc)
            return [], after

    def _follow(self, path: Path, after: str | None) -> tuple[list[LogEntry], str | None]:
        try:
            cur = path.stat()
        except FileNotFoundError:
            return [], after
        ino, off = _parse_cursor(after)
        first = ino is None
        target = path
        if first:
            ino, off = cur.st_ino, max(0, cur.st_size - _FIRST_READ_BYTES)
        elif ino != cur.st_ino:
            # Rotated: finish the old file if it is still around, then start the new one.
            old = _find_by_inode(path, ino)
            if old is None:
                ino, off = cur.st_ino, 0
            else:
                target = old
        elif cur.st_size < off:  # truncated in place
            off = 0

        entries, new_off, more = self._read_entries(target, off, skip_partial=first and off > 0)
        if target is not path and not more:
            # The old file is drained; carry on at the start of the live one.
            tail, tail_off, _ = self._read_entries(path, 0, budget=self._max_lines - len(entries))
            return entries + tail, f"{cur.st_ino}:{tail_off}"
        return entries, f"{ino}:{new_off}"

    def _read_entries(
        self, path: Path, offset: int, *, skip_partial: bool = False, budget: int | None = None,
    ) -> tuple[list[LogEntry], int, bool]:
        """Whole entries from *offset*; stops at the budget on an entry boundary.

        Returns the entries, the offset after them, and whether more is waiting.
        """
        budget = self._max_lines if budget is None else budget
        entries: list[LogEntry] = []
        if budget <= 0:
            return entries, offset, True
        current: list[str] = []
        pos = offset
        with path.open("rb") as fh:
            fh.seek(offset)
            while True:
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):
                    break  # EOF, or a line still being written
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                line_start = pos
                pos += len(raw)
                header = _AGENT_LINE.match(text)
                if skip_partial:
                    if not header:
                        continue
                    skip_partial = False
                if header:
                    if current:
                        self._emit(entries, current)
                        if len(entries) >= budget:
                            return entries, line_start, True
                    current = [text]
                elif current:
                    current.append(text)  # a traceback belongs to its entry
                else:
                    current = [text]
        if current:
            self._emit(entries, current)
        return entries, pos, False

    @staticmethod
    def _emit(entries: list[LogEntry], lines: list[str]) -> None:
        text = "\n".join(lines)
        header = _AGENT_LINE.match(lines[0])
        if header is None:
            entries.append(LogEntry(timestamp_ns=time.time_ns(), line=text))
            return
        logger = header.group(4)
        if logger.startswith(_SELF_LOGGERS):
            return
        try:
            ts_ns = int(datetime.strptime(header.group(1), "%Y-%m-%d %H:%M:%S").timestamp() * 1e9) + int(header.group(2)) * 1_000_000
        except ValueError:
            ts_ns = time.time_ns()
        name = header.group(3).upper()
        level = "error" if name in ("ERROR", "CRITICAL", "FATAL") else "warning" if name.startswith("WARN") else "debug" if name == "DEBUG" else "info"
        entries.append(LogEntry(timestamp_ns=ts_ns, line=text, level=level))

    # ── Windows: wevtutil ────────────────────────────────────

    def _collect_windows(self, after: str | None) -> tuple[list[LogEntry], str | None]:
        import subprocess
        import xml.etree.ElementTree as ET

        # Build XPath query for System log
        newest_first = not after
        if after:
            # Oldest first from the position: newest-first with a count gave
            # the last N events after it, skipping the middle of a backlog.
            xpath = (
                f"*[System[TimeCreated[@SystemTime>'{after}']]]"
            )
            cmd = [
                "wevtutil", "qe", "System",
                f"/q:{xpath}",
                f"/c:{self._max_lines}",
                "/f:xml",
                "/rd:false",
            ]
        else:
            cmd = [
                "wevtutil", "qe", "System",
                f"/c:{self._max_lines}",
                "/f:xml",
                "/rd:true",
            ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            log.debug("wevtutil not found — system log collection disabled.")
            return [], after
        except subprocess.TimeoutExpired:
            log.warning("wevtutil timed out.")
            return [], after

        if result.returncode != 0:
            log.debug("wevtutil exited %d: %s", result.returncode, result.stderr[:200])
            return [], after

        entries: list[LogEntry] = []
        max_ts: str | None = None

        # wevtutil outputs multiple <Event> elements without a root
        wrapped = f"<Events>{result.stdout}</Events>"
        try:
            root = ET.fromstring(wrapped)
        except ET.ParseError:
            log.debug("Failed to parse wevtutil XML output.")
            return [], after

        ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

        for event in root.findall(".//e:Event", ns):
            system = event.find("e:System", ns)
            if system is None:
                continue

            # Timestamp
            tc = system.find("e:TimeCreated", ns)
            ts_str = tc.get("SystemTime", "") if tc is not None else ""
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts_ns = int(dt.timestamp() * 1_000_000_000)
                except ValueError:
                    ts_ns = time.time_ns()
                if max_ts is None or ts_str > max_ts:
                    max_ts = ts_str
            else:
                ts_ns = time.time_ns()

            # Level
            level_el = system.find("e:Level", ns)
            level = _win_level(int(level_el.text) if level_el is not None and level_el.text else 4)

            # Provider + EventID for the log line
            provider_el = system.find("e:Provider", ns)
            provider = provider_el.get("Name", "System") if provider_el is not None else "System"
            eid_el = system.find("e:EventID", ns)
            eid = eid_el.text if eid_el is not None and eid_el.text else "0"

            # Message data
            event_data = event.find("e:EventData", ns)
            msg_parts: list[str] = []
            if event_data is not None:
                for data in event_data.findall("e:Data", ns):
                    if data.text:
                        msg_parts.append(data.text)
            message = " ".join(msg_parts) if msg_parts else f"EventID={eid}"

            line = f"{ts_str} {provider}[{eid}]: {message}"
            entries.append(LogEntry(timestamp_ns=ts_ns, line=line, level=level))

        if newest_first:
            # Chronological: /rd:true reads newest first.
            entries.reverse()
        return entries, max_ts or after


def _parse_cursor(after: str | None) -> tuple[int | None, int]:
    try:
        ino, off = (after or "").split(":")
        return int(ino), int(off)
    except ValueError:
        return None, 0


def _find_by_inode(path: Path, ino: int) -> Path | None:
    """The rotated-away copy of *path* (``agent.log.0`` ...) that has inode *ino*, if still uncompressed."""
    try:
        for cand in path.parent.glob(path.name + ".*"):
            if cand.suffix not in (".gz", ".bz2") and cand.stat().st_ino == ino:
                return cand
    except OSError:
        pass
    return None


def _win_level(level_id: int) -> str:
    """Map Windows event level integer to string."""
    return {
        1: "error",    # Critical
        2: "error",    # Error
        3: "warning",  # Warning
        4: "info",     # Information
        5: "debug",    # Verbose
    }.get(level_id, "info")
