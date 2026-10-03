"""Ship Ray Serve replica logs, and the engine output behind them, to Loki.

The Ray runtime container idles on ``sleep infinity``. Every Serve replica
writes to its own file under ``session_latest/logs/serve/`` inside it, so
``docker logs`` on that container is permanently empty and the only record of
what a replica did lives in those files. The deployment log panel showed
nothing at all for a deployment that was serving.

Reading them on demand works (the agent can tail them through a command) but
costs a round trip per page and cannot stream. Shipping them continuously
turns the panel into a Loki query, the same as the node's own logs, and gives
live tailing for free.

This reads ordinary files rather than shelling into the container, which is
what the session-directory bind mount in the certified bundle is for. Tailing
is ``O_RDONLY`` and cannot block Ray's writers -- Linux has no mandatory
locking. The two hazards are rotation and disk:

* **Rotation** is handled by keying cursors on ``(device, inode)`` rather than
  on the path. When Ray rotates a file the path is reused by a new inode; a
  path-keyed cursor would resume at the old offset into a new file and skip or
  duplicate lines. A file whose inode changed is read from the beginning.
* **Disk** is the bundle's problem, not this one's: ``RAY_ROTATION_MAX_BYTES``
  and ``RAY_ROTATION_BACKUP_COUNT`` are set alongside the mount, because
  raylet and worker logs do not rotate by default and a persisted session
  directory without a bound is how a node's disk fills.

Two streams per replica, told apart by the ``source`` label:

* ``replica``: Serve's own log for it (``logs/serve/replica_*.log``).
* ``engine``: what the replica's process prints -- vLLM's engine runs as its
  child and writes there -- in Ray's ``logs/worker-<worker id>-*.out|err``.
  A failed start says "Engine core initialization failed. See root cause
  above" in the replica log; the root cause is only here. The worker id is
  read from the replica log's own lines, so the two are linked exactly.

Delivery: a file's cursor moves only past lines Loki accepted. A failed push
is sent again on the next pass, and a burst drains at ``_MAX_READ_BYTES`` per
file per pass rather than being dropped -- the file is the queue
(``log_cursors``). Rotated files (``.log.1`` ...) are read to their end. The
one loss left is Ray deleting a rotated file before it was sent, and that is
said, with a line in the stream, rather than silent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from faden_agent.log_cursors import LogCursors
from faden_agent.loki_client import LokiClient

log = logging.getLogger(__name__)

#: Where Ray keeps a session's Serve logs, inside the runtime container and --
#: once the bundle's mount is in place -- on the host.
DEFAULT_SESSION_DIR = "/var/lib/faden/ray"

#: ``replica_<app>_<deployment>_<replica id>.log``, and its rotations (``.1`` ...).
#:
#: The app name is ours (``faden-<deployment uuid>``) and contains hyphens,
#: the deployment name is Ray's and may contain underscores, so the split is
#: anchored on the known app prefix rather than on counting underscores.
_REPLICA_FILE = re.compile(r"^replica_(?P<rest>.+)\.log(?:\.\d+)?$")
#: A Ray worker process's console, and its rotations.
_WORKER_FILE = re.compile(r"^worker-[0-9a-f]+-.+\.(?:out|err)(?:\.\d+)?$")
#: The Ray worker id a replica log's JSON lines carry.
_WORKER_ID = re.compile(rb'"worker_id":\s*"(?P<id>[0-9a-f]{16,})"')
#: How much of a replica log to read for its worker id.
_HEAD_BYTES = 64 * 1024
#: Terminal colour codes Ray puts around its log prefixes.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: ``(EngineCore pid=13728) (RayWorkerProc pid=8963) `` -- the prefixes Ray and
#: vLLM put before a child's line.
_PREFIXES = re.compile(r"^(?:\([A-Za-z_][\w:.-]* pid=\d+(?:, ip=[\d.]+)?\)\s*)+")
#: vLLM's own line: ``ERROR 09-30 06:44:12 [core.py:1349] ...``.
_VLLM_LINE = re.compile(
    r"^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) (?P<month>\d\d)-(?P<day>\d\d) (?P<time>\d\d:\d\d:\d\d)"
)
#: An exception's last line (``ValueError: ...``) or a traceback's first.
_ERROR_LINE = re.compile(r"^(?:Traceback \(most recent call last\)|[\w.]*(?:Error|Exception)\b.*:)")

#: Read at most this much from one file per pass.
#:
#: A replica that logs a stack trace per request can produce megabytes between
#: passes, and a single unbounded read would hold the loop and the memory for
#: all of it. The remainder is picked up next pass: the cursor advances by
#: what was actually read.
_MAX_READ_BYTES = 1 * 1024 * 1024

#: Lines in one push. Loki rejects very large batches, and a replica that has
#: just started can emit thousands at once.
_MAX_BATCH_LINES = 500


class RayServeLogForwarder:
    """Tail Ray Serve replica log files and push them to Loki."""

    def __init__(
        self,
        *,
        loki: LokiClient,
        host: str,
        session_dir: str = DEFAULT_SESSION_DIR,
        interval_sec: int = 5,
        cursors: LogCursors | None = None,
    ) -> None:
        self._loki = loki
        self._host = host
        self._session_dir = Path(session_dir)
        self._interval = max(interval_sec, 2)
        self._store = cursors or LogCursors(None)
        #: ``(device, inode) -> byte offset`` of the next unsent line. Keyed on
        #: identity rather than path, so a rotated file (renamed, same inode)
        #: is finished where it was, and a new one at the old path starts at 0.
        self._cursors: dict[tuple[int, int], int] = {
            (int(dev), int(ino)): int(offset)
            for key, offset in self._store.with_prefix("ray:").items()
            for _, dev, ino in [key.split(":")]
        }
        #: What each followed file is, and how big it last was: a file that
        #: disappears with bytes unsent is reported, not forgotten silently.
        self._seen: dict[tuple[int, int], tuple[str, dict[str, str], int]] = {}
        #: Replica log -> the Ray worker id of its process (read once).
        self._worker_ids: dict[tuple[int, int], str | None] = {}
        #: File -> the timestamp its last sent line went to Loki with. Lines
        #: are stamped strictly later than the line before them in the same
        #: file, so Loki gives them back in the order they were written.
        self._last_ts: dict[tuple[int, int], int] = {}
        #: Whether a pass has completed.
        #:
        #: The distinction this draws is the difference between skipping a
        #: cluster's whole history and losing a replica's first seconds. At
        #: startup an unknown file may hold days of logs, so it is read from
        #: the end. Afterwards an unknown file is one that appeared while we
        #: were watching -- a new replica, or a rotation -- so it is read from
        #: the beginning, because it cannot have a history we missed. An agent
        #: that saved cursors before a restart is past its first pass: a file
        #: it does not know appeared while it was down.
        self._started = self._store.resumed

    async def run_forever(self) -> None:
        while True:
            try:
                await self._collect_all()
            except Exception:  # noqa: BLE001 - a log cycle never kills the agent
                log.debug("Ray Serve log collection cycle failed.", exc_info=True)
            await asyncio.sleep(self._interval)

    # ── discovery ────────────────────────────────────────────────────────

    def _serve_log_dir(self) -> Path | None:
        """The running session's ``logs/serve``, if there is one.

        ``session_latest`` is a symlink Ray maintains, and it is tempting to
        just follow it. It does not work from here: Ray writes the link with
        an **absolute path**, and that path is the one inside the container
        (``/tmp/ray/session_...``). Read through the host side of the bind
        mount it points at a directory that does not exist, so the link
        resolves to nothing and the forwarder silently found no logs at all.

        So the newest ``session_*`` directory is used instead, which is a real
        directory on both sides of the mount. The symlink is still tried
        first, because it is correct when this runs inside the container and
        it is the cheaper answer.

        A node not running Ray simply has no directory, which is not an error.
        """
        linked = self._session_dir / "session_latest" / "logs" / "serve"
        if linked.is_dir():
            return linked

        sessions = [
            path
            for path in self._session_dir.glob("session_*")
            if path.is_dir() and not path.is_symlink()
        ]
        if not sessions:
            return None
        # Newest by mtime: a node that has hosted several clusters keeps the
        # old session directories, and the live one is the one being written.
        newest = max(sessions, key=lambda path: path.stat().st_mtime)
        candidate = newest / "logs" / "serve"
        return candidate if candidate.is_dir() else None

    async def _collect_all(self) -> None:
        serve_dir = self._serve_log_dir()
        if serve_dir is None:
            return

        followed: dict[tuple[int, int], tuple[Path, dict[str, str]]] = {}
        for path in sorted(serve_dir.glob("replica_*.log*")):
            meta = self._parse_filename(path.name)
            key = await self._key_of(path) if meta else None
            if key is None:
                continue
            followed[key] = (path, {**meta, "source": "replica"})
            worker_id = await self._worker_id(key, path)
            if not worker_id:
                continue
            for engine in sorted(serve_dir.parent.glob(f"worker-{worker_id}-*")):
                engine_key = await self._key_of(engine) if _WORKER_FILE.match(engine.name) else None
                if engine_key is not None:
                    followed[engine_key] = (engine, {**meta, "source": "engine"})

        for key, (path, meta) in followed.items():
            try:
                await self._tail_file(path, key, meta)
            except Exception:  # noqa: BLE001 - one bad file is not the rest
                log.debug("Failed to tail %s", path, exc_info=True)

        # Gone: rotated away by Ray, or its replica's session ended. Bytes
        # still unsent are lost with it -- say so, in the stream it was part of.
        for key in set(self._cursors) - set(followed):
            offset = self._cursors.pop(key)
            self._last_ts.pop(key, None)
            name, meta, size = self._seen.pop(key, ("", {}, offset))
            if size > offset and meta:
                await self._push(
                    [f"[faden-agent] {size - offset} bytes of {name} were rotated away before they could be sent"],
                    meta,
                )
        for key in set(self._worker_ids) - set(followed):
            self._worker_ids.pop(key, None)

        self._started = True
        self._store.replace_prefix(
            "ray:", {f"ray:{dev}:{ino}": offset for (dev, ino), offset in self._cursors.items()},
        )
        self._store.save()

    async def _key_of(self, path: Path) -> tuple[int, int] | None:
        try:
            return await asyncio.to_thread(self._identity, path)
        except OSError:
            return None

    async def _worker_id(self, key: tuple[int, int], path: Path) -> str | None:
        """The Ray worker id of the process a replica log belongs to, from its first lines."""
        if self._worker_ids.get(key) is None:
            def head() -> bytes:
                with path.open("rb") as handle:
                    return handle.read(_HEAD_BYTES)

            try:
                match = _WORKER_ID.search(await asyncio.to_thread(head))
            except OSError:
                match = None
            self._worker_ids[key] = match.group("id").decode() if match else None
        return self._worker_ids[key]

    @staticmethod
    def _identity(path: Path) -> tuple[int, int] | None:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino)

    # ── tailing ──────────────────────────────────────────────────────────

    async def _tail_file(self, path: Path, key: tuple[int, int], meta: dict[str, str]) -> None:
        offset = self._cursors.get(key)
        size = await asyncio.to_thread(lambda: path.stat().st_size)
        self._seen[key] = (path.name, meta, size)

        if offset is None:
            if not self._started:
                # Our first pass. This file may hold days of logs from before
                # the agent started; shipping them would be a burst of stale
                # lines into Loki all stamped with today's time. Start at the
                # end.
                self._cursors[key] = size
                return
            # It appeared while we were watching -- a replica that just
            # started, or a rotation reusing the path with a new inode. It
            # cannot have a history we missed, so read it whole. Skipping to
            # the end here would silently drop a replica's first seconds,
            # which is exactly the part worth reading when one fails to come
            # up.
            offset = 0

        if size < offset:
            # Truncated in place (``> file``, or a rotation that reused the
            # inode). Anything before the new size is gone; start over.
            offset = 0
        self._cursors[key] = offset

        if size == offset:
            return

        lines = await asyncio.to_thread(
            self._read_from, path, offset, min(size - offset, _MAX_READ_BYTES)
        )
        for i in range(0, len(lines), _MAX_BATCH_LINES):
            batch = lines[i : i + _MAX_BATCH_LINES]
            if not await self._push([text for text, _ in batch], meta, key=key):
                # Loki (or the way to it) did not take them: the cursor stays,
                # and the next pass sends them again.
                return
            offset += sum(size for _, size in batch)
            self._cursors[key] = offset

    @staticmethod
    def _read_from(path: Path, offset: int, length: int) -> list[tuple[str, int]]:
        """The whole lines in *length* bytes from *offset*, each with its size in bytes.

        A pass can land mid-line. Shipping the fragment would split one log
        entry across two Loki entries and break its JSON; instead the read
        stops at the last newline, and the partial line is picked up whole on
        the next pass. The sizes let the cursor move past exactly the lines
        that were sent.
        """
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(length)
        cut = raw.rfind(b"\n")
        if cut == -1:
            # No complete line yet -- wait rather than ship a fragment.
            return []
        lines: list[tuple[str, int]] = []
        for chunk in raw[: cut + 1].split(b"\n")[:-1]:
            size = len(chunk) + 1
            text = chunk.decode("utf-8", errors="replace").rstrip("\r")
            if text.strip():
                lines.append((text, size))
            elif lines:
                # An empty line still moves the cursor.
                lines[-1] = (lines[-1][0], lines[-1][1] + size)
            else:
                lines.append(("", size))
        return lines

    # ── labelling ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_filename(name: str) -> dict[str, str]:
        """Pull the app, deployment and replica out of the file name.

        Ray names these ``replica_<app>_<deployment>_<replica id>.log``. The
        app is ours and contains hyphens; the deployment is Ray's and may
        contain underscores; the replica id is the last segment. Splitting
        from the right is the only part that is reliable, so that is what
        this does -- and every field is confirmed against the JSON body when
        the line carries one.
        """
        match = _REPLICA_FILE.match(name)
        if not match:
            return {}
        rest = match.group("rest")
        parts = rest.rsplit("_", 1)
        if len(parts) != 2:
            return {"replica": rest}
        head, replica = parts
        app, _, deployment = head.partition("_")
        out = {"replica": replica}
        if app:
            out["app"] = app
        if deployment:
            out["deployment"] = deployment
        return out

    async def _push(
        self, lines: list[str], meta: dict[str, str], *, key: tuple[int, int] | None = None,
    ) -> bool:
        """Send one batch, splitting it by level so the label is accurate; True when Loki took all of it.

        Loki labels belong to a stream, not to a line, so a batch containing
        both an INFO and an ERROR cannot carry one ``level``. Grouping by
        level keeps the label true; the cost is at most a handful of small
        pushes instead of one, and in practice a batch is nearly all one
        level.

        Every line of file *key* is stamped strictly later than the one
        before it. vLLM stamps to the second, and a traceback's lines took
        that stamp plus a nanosecond each: two requests failing in the same
        second gave their tracebacks the same times, and they came back from
        Loki interleaved line by line -- six "Traceback" lines, then six
        first frames.
        """
        by_level: dict[str, list[tuple[int, str]]] = {}
        engine = meta.get("source") == "engine"
        floor = self._last_ts.get(key) if key is not None else None
        last_ts: int | None = None
        for line in lines:
            if not line:
                continue
            if engine:
                line, ts_ns, level = self._read_engine_line(line, last_ts)
            else:
                ts_ns, level = self._read_fields(line)
            if floor is not None and ts_ns <= floor:
                ts_ns = floor + 1
            floor = last_ts = ts_ns
            by_level.setdefault(level, []).append((ts_ns, line))

        accepted = True
        for level, entries in by_level.items():
            labels = {
                # Deliberately its own job rather than "node-agent": these are
                # the workload's logs, and an operator reading them should not
                # have to filter the agent's out.
                "job": "ray-serve",
                "host": self._host,
                "level": level,
                **meta,
            }
            accepted = await self._loki.push_streams(labels=labels, entries=entries) and accepted
        # Only once Loki has them: a batch sent again is stamped the same,
        # and Loki keeps one of each.
        if accepted and key is not None and floor is not None:
            self._last_ts[key] = floor
        return accepted

    @staticmethod
    def _read_engine_line(line: str, last_ts: int | None) -> tuple[str, int, str]:
        """An engine process's plain line: without colour codes, with a timestamp and a level.

        vLLM stamps its lines (``ERROR 09-30 06:44:12 [core.py:1349] ...``,
        with no year); a traceback's lines carry no time, and take the one
        before them so they stay in order. Read long after it was written --
        a backlog after an outage -- a line keeps its own time rather than
        the moment it was sent.
        """
        text = _ANSI.sub("", line)
        body = _PREFIXES.sub("", text)
        now = datetime.now(tz=UTC)
        ts_ns = (last_ts + 1) if last_ts is not None else int(now.timestamp() * 1_000_000_000)
        level = "unknown"
        match = _VLLM_LINE.match(body)
        if match:
            level = match.group("level").lower()
            try:
                stamped = datetime.fromisoformat(
                    f"{now.year}-{match.group('month')}-{match.group('day')}T{match.group('time')}+00:00"
                )
                if stamped > now.replace(microsecond=0) and (stamped - now).days >= 1:
                    stamped = stamped.replace(year=now.year - 1)  # read across New Year
                ts_ns = int(stamped.timestamp() * 1_000_000_000)
            except ValueError:
                pass
        elif _ERROR_LINE.match(body):
            level = "error"
        return text, ts_ns, level

    @staticmethod
    def _read_fields(line: str) -> tuple[int, str]:
        """Timestamp and level from a JSON line, with plain-text fallbacks.

        The compiler asks Serve for JSON-encoded logs, so the common case is a
        parsed object. A line from before that setting took effect, or from a
        component that writes plain text, still has to ship -- it just carries
        the arrival time and an unknown level rather than being dropped.
        """
        now_ns = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
        stripped = line.lstrip()
        if not stripped.startswith("{"):
            return now_ns, "unknown"
        try:
            doc = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return now_ns, "unknown"
        if not isinstance(doc, dict):
            return now_ns, "unknown"

        level = str(doc.get("levelname") or doc.get("level") or "unknown").lower()

        raw_ts = doc.get("asctime") or doc.get("timestamp") or doc.get("time")
        ts_ns = now_ns
        if isinstance(raw_ts, (int, float)):
            # Seconds since the epoch, as Ray's JSON formatter emits.
            ts_ns = int(float(raw_ts) * 1_000_000_000)
        elif isinstance(raw_ts, str):
            try:
                # Ray's JSON formatter writes logging's default asctime:
                # "2026-09-22 09:48:00,673". The comma is a millisecond
                # separator and ``fromisoformat`` rejects it, so every line
                # silently fell back to its arrival time -- close enough to
                # look right and wrong enough to reorder a burst.
                parsed = datetime.fromisoformat(
                    raw_ts.replace(",", ".").replace("Z", "+00:00")
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                ts_ns = int(parsed.timestamp() * 1_000_000_000)
            except (ValueError, OverflowError):
                ts_ns = now_ns
        return ts_ns, level


__all__ = ["RayServeLogForwarder", "DEFAULT_SESSION_DIR"]
