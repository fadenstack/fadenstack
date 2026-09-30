"""A Ray deployment's logs, read from Loki, in the driver-neutral contract (Phase 6, WI-2).

The agents ship everything to Loki as it is written (``ray_log_forwarder``,
through this backend, never dropping a line: the log on disk is their queue),
so a page is one query here. It used to be a command to a machine that
tailed files on demand, with a Loki read in front that ignored which source
and machine the page asked for. Now the choice is the query:

* ``serve_replica`` -- Serve's log of the replicas;
* ``engine`` -- what vLLM's engine printed, where a failed start's root cause
  is (``{source="engine"}``);
* ``agent`` -- the machines' agents.

and a machine narrows it by the ``host`` label the agents ship under.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from faden_backend.db.models.inference import InferenceDeployment
from faden_backend.services.inference.observability import (
    LogLine,
    LogPage,
    LogSource,
)

log = logging.getLogger(__name__)

_MAX_TAIL = 5000
#: How far back a page looks. A deployment that failed this morning is still
#: worth reading this afternoon; the tail keeps the page small.
_WINDOW = timedelta(hours=24)
#: An exception's last line, after Ray's and vLLM's process prefixes:
#: ``(EngineCore pid=13728) ValueError: There is no module ...``.
_PREFIXES = re.compile(r"^(?:\([A-Za-z_][\w:.-]* pid=\d+(?:, ip=[\d.]+)?\)\s*)+")
_EXCEPTION = re.compile(r"^(?P<cause>[A-Za-z_][\w.]*(?:Error|Exception)\b(?::\s.*)?)$")


def _escape_label(value: str) -> str:
    """Make a value safe inside a LogQL double-quoted matcher.

    These come from our own records (an app name, a Ray replica id, a host),
    but a stray quote would turn a selector into a syntax error at best.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def log_selector(
    source: LogSource, *, app_name: str, hosts: list[str], replica_id: str | None = None,
) -> str:
    """The LogQL stream selector for one page."""
    matchers: list[str]
    if source == LogSource.AGENT:
        matchers = ['job="node-agent"']
    else:
        matchers = ['job="ray-serve"', f'app="{_escape_label(app_name)}"']
        # Streams shipped before the source label existed are the replica's.
        matchers.append('source="engine"' if source == LogSource.ENGINE else 'source!="engine"')
        if replica_id:
            matchers.append(f'replica="{_escape_label(replica_id)}"')
    if len(hosts) == 1:
        matchers.append(f'host="{_escape_label(hosts[0])}"')
    elif hosts:
        matchers.append('host=~"' + "|".join(_escape_label(re.escape(host)) for host in hosts) + '"')
    return "{" + ", ".join(matchers) + "}"


async def _query(selector: str, *, limit: int, start: datetime) -> dict[str, Any]:
    """One ``query_range``, newest first. Raises when Loki cannot be asked."""
    from faden_backend.web.api.logs.views import _request_loki_json  # noqa: PLC0415

    now = datetime.now(tz=UTC)
    return await _request_loki_json(
        "/loki/api/v1/query_range",
        params={
            "query": selector,
            "start": str(int(start.timestamp() * 1_000_000_000)),
            "end": str(int(now.timestamp() * 1_000_000_000)),
            "limit": str(limit),
            "direction": "BACKWARD",
        },
        timeout=5.0,
    )


class RayLogReader:
    """A page of a deployment's logs from Loki."""

    async def read(
        self,
        deployment: InferenceDeployment,
        *,
        source: LogSource,
        app_name: str,
        hosts: list[str],
        replica_id: str | None = None,
        tail: int = 200,
    ) -> LogPage:
        """The last *tail* lines of *source* for *deployment*, on *hosts* (all of the cluster's by default)."""
        tail = max(1, min(int(tail or 200), _MAX_TAIL))
        if source == LogSource.RUNTIME_CONTAINER:
            source = LogSource.SERVE_REPLICA  # the old name for the same page
        selector = log_selector(source, app_name=app_name, hosts=hosts, replica_id=replica_id)
        try:
            payload = await _query(selector, limit=tail, start=datetime.now(tz=UTC) - _WINDOW)
        except Exception as exc:  # noqa: BLE001 - a log read never fails a request
            log.warning("Log read for deployment %s failed: %s", deployment.id, exc)
            return LogPage(source=source, replica_id=replica_id, detail=f"Loki could not be asked: {exc}")
        lines = _lines_from_loki(payload)
        return LogPage(
            source=source,
            replica_id=replica_id,
            # Oldest first: Loki answers newest-first for a BACKWARD query,
            # and a log panel reads downwards.
            lines=lines[-tail:],
            truncated=len(lines) >= tail,
            detail=None if lines else "nothing logged here in the last 24 hours",
        )


async def engine_root_cause(app_name: str, *, since: timedelta = timedelta(hours=1)) -> str | None:
    """The last exception vLLM's engine printed for *app_name*, and the machine it was on.

    When a replica fails to start, Serve only reports "Engine core
    initialization failed. See root cause above" -- the root cause is in the
    engine's own output, which the agents ship as ``source="engine"``.
    ``None`` when there is none to be found (or Loki cannot be asked): the
    caller keeps what Serve said.
    """
    selector = log_selector(LogSource.ENGINE, app_name=app_name, hosts=[])
    try:
        payload = await _query(
            selector + ' |~ "(Error|Exception)"', limit=200, start=datetime.now(tz=UTC) - since,
        )
    except Exception:  # noqa: BLE001 - the cause is a nicety on top of Serve's message
        log.debug("Engine root cause for %s not read", app_name, exc_info=True)
        return None
    for line in reversed(_lines_from_loki(payload)):
        match = _EXCEPTION.match(_PREFIXES.sub("", line.message).strip())
        # Serve's own summary is not the cause it points at.
        if match and "Engine core initialization failed" not in match.group("cause"):
            return f"{match.group('cause')} (on {line.host})" if line.host else match.group("cause")
    return None


def _lines_from_loki(payload: dict[str, Any]) -> list[LogLine]:
    """Normalise a Loki ``query_range`` body into log lines, oldest first.

    Each entry is ``[<nanoseconds as a string>, <line>]``. The line is JSON
    when it came from a Serve replica -- the compiler asks for JSON encoding
    -- so the level and message are read from fields rather than parsed out
    of formatted text. Anything else ships as-is rather than being dropped.
    """
    result = ((payload or {}).get("data") or {}).get("result") or []
    out: list[LogLine] = []
    for stream in result:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") or {}
        for value in stream.get("values") or []:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            raw_ts, raw_line = value[0], value[1]
            try:
                ts = datetime.fromtimestamp(int(raw_ts) / 1_000_000_000, tz=UTC)
            except (ValueError, TypeError, OverflowError):
                ts = datetime.now(tz=UTC)

            level = str(labels.get("level") or "").upper() or None
            if level == "UNKNOWN":
                level = None
            message = str(raw_line)
            stripped = message.lstrip()
            if stripped.startswith("{"):
                try:
                    doc = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    doc = None
                if isinstance(doc, dict):
                    message = str(doc.get("message") or message)
                    level = str(doc.get("levelname") or level or "").upper() or None
            out.append(LogLine(ts=ts, level=level, message=message, host=labels.get("host")))
    out.sort(key=lambda line: line.ts)
    return out
