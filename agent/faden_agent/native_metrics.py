"""The Mac's metrics-only pass-through: ``GET /metrics`` to the running worker's ``/metrics``.

A Linux node's engine metrics are scraped by central Prometheus as plain HTTP
``/metrics``. A Mac has no such endpoint (its workers listen on loopback only),
so the agent offers exactly one: this listener forwards a single ``GET /metrics``
to ``127.0.0.1:<the running worker's internal port>`` and streams the answer
back. It does not parse, store, relabel or push anything, and it refuses every
other path and method. With no worker running it answers 503.

Same exposure as Linux: plain HTTP, aggregate counters and histograms only
(no prompt or completion text is in a vLLM ``/metrics`` page). One port per node
(a Mac runs one model); it binds where the model proxy binds. It runs on its own
event loop in a thread, so the agent's other work cannot stall a scrape.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PORT = 9468  # outside the proxy's P (8100-8999) and P+20000 (28100-28999) ranges
#: The port is unauthenticated and shares the agent's process (its FDs, the control stream): hard caps.
MAX_CONNECTIONS = 16
MAX_PER_IP = 4
_MAX_HEAD = 8192
_HEAD_TIMEOUT = 10.0
_CONNECT_TIMEOUT = 3.0
_IDLE_TIMEOUT = 30.0
_TOTAL_TIMEOUT = 120.0
_CHUNK = 64 * 1024


class MetricsPassthrough:
    """``port_source()`` returns the running worker's loopback port, or ``None`` when none runs."""

    def __init__(
        self, port_source: Callable[[], int | None], *, port: int = DEFAULT_PORT,
        bind_hosts: tuple[str, ...] = ("0.0.0.0", "::"),  # noqa: S104 - where the model proxy binds
        max_connections: int = MAX_CONNECTIONS, max_per_ip: int = MAX_PER_IP,
    ) -> None:
        self._max = max_connections
        self._max_ip = max_per_ip
        self._open = 0  # every accepted connection, from accept to close (head phase included)
        self._per_ip: dict[str, int] = {}
        self._port_source = port_source
        self._wanted = port
        self._hosts = bind_hosts
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._servers: list[asyncio.AbstractServer] = []
        self._ready = threading.Event()
        self._error: Exception | None = None
        self.port: int = 0

    def start(self) -> None:
        """Bind and serve; raises ``OSError`` when no address could be bound."""
        self._thread = threading.Thread(target=self._run, name="native-metrics", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._error is not None:
            raise self._error
        if not self._servers:
            raise OSError("the metrics pass-through did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._bind())
        except Exception as exc:  # noqa: BLE001 - reported by start()
            self._error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            for server in self._servers:
                server.close()
            with contextlib.suppress(Exception):
                loop.run_until_complete(asyncio.gather(*(s.wait_closed() for s in self._servers)))
            loop.close()

    async def _bind(self) -> None:
        port = self._wanted
        last: OSError | None = None
        for host in self._hosts:
            try:
                server = await asyncio.start_server(
                    self._serve, host, port, reuse_address=True, family=socket.AF_INET6 if ":" in host else socket.AF_INET,
                )
            except OSError as exc:  # IPv6 may be absent; any one address is enough
                last = exc
                continue
            self._servers.append(server)
            port = server.sockets[0].getsockname()[1]  # port 0 in tests: both families share one
        self.port = port
        if not self._servers:
            raise last or OSError("no address to bind")

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(5)

    # ── one connection ──────────────────────────────────────────────

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        ip = str(peer[0])
        if self._open >= self._max or self._per_ip.get(ip, 0) >= self._max_ip:
            writer.transport.abort()  # over the cap: closed at once, nothing read
            return
        self._open += 1
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        try:
            await asyncio.wait_for(self._handle(reader, writer), _TOTAL_TIMEOUT)
        except (TimeoutError, ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        except Exception:  # noqa: BLE001 - one bad connection never stops the listener
            log.debug("Metrics pass-through: connection failed", exc_info=True)
        finally:
            self._open -= 1
            if self._per_ip[ip] <= 1:
                del self._per_ip[ip]
            else:
                self._per_ip[ip] -= 1
            with contextlib.suppress(Exception):
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 2)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError):
            return
        if len(head) > _MAX_HEAD:
            await _reply(writer, 431, "request header too large")
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            await _reply(writer, 400, "malformed request")
            return
        method, target, _version = parts
        path = target.split("?", 1)[0]
        if not target.startswith("/") or path != "/metrics":
            await _reply(writer, 403, "this path is not served here")
            return
        if method != "GET":
            await _reply(writer, 405, "method not allowed", [("Allow", "GET")])
            return
        internal = self._port_source()
        if internal is None:
            await _reply(writer, 503, "no model is running on this machine")
            return
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", internal), _CONNECT_TIMEOUT)
        except (OSError, TimeoutError):
            await _reply(writer, 503, "the model server is not reachable")
            return
        try:
            # The request is built here; nothing the client sent is forwarded.
            uw.write(
                f"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1:{internal}\r\nAccept: */*\r\n"
                "Accept-Encoding: identity\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            await uw.drain()
            while True:  # the worker's answer, byte for byte, with backpressure
                chunk = await asyncio.wait_for(ur.read(_CHUNK), _IDLE_TIMEOUT)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        finally:
            with contextlib.suppress(Exception):
                uw.transport.abort()


async def _reply(writer: asyncio.StreamWriter, status: int, message: str, extra: list[tuple[str, str]] | None = None) -> None:
    reasons: dict[int, str] = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 431: "Request Header Fields Too Large", 503: "Service Unavailable"}
    body = (message + "\n").encode()
    head = [f"HTTP/1.1 {status} {reasons.get(status, 'Error')}", "Content-Type: text/plain; charset=utf-8",
            f"Content-Length: {len(body)}", "Connection: close"]
    head += [f"{k}: {v}" for k, v in (extra or [])]
    with contextlib.suppress(Exception):
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body)
        await asyncio.wait_for(writer.drain(), 5)


def worker_port(manager: Any) -> int | None:
    """The loopback port of the one running (ready) native worker, else ``None``."""
    for record in manager._records():  # noqa: SLF001 - the manager's own scan of its worker records
        if record.get("state") == "ready" and manager._leader(record) is not None:  # noqa: SLF001
            try:
                return int(record["port"])
            except (KeyError, TypeError, ValueError):
                return None
    return None
