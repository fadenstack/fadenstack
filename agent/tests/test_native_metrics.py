"""The Mac's metrics-only pass-through: only ``GET /metrics``, the worker's page unchanged, 503 with no worker."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Iterator

import pytest

from faden_agent.native_metrics import DEFAULT_PORT, MetricsPassthrough, worker_port

BIG = b"# HELP vllm:x x\n" + b"vllm:num_requests_running{model_name=\"m\",engine=\"0\"} 1.0\n" * 120_000  # ~7 MB


class _Upstream:
    """A loopback worker answering ``/metrics`` (and recording what it was asked)."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[bytes] = []
        self._loop = asyncio.new_event_loop()
        self.port = 0
        started = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self._loop)

            async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
                self.requests.append(await r.readuntil(b"\r\n\r\n"))
                w.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: %d\r\nconnection: close\r\n\r\n" % len(self.body))
                w.write(self.body)
                await w.drain()
                w.close()

            async def main() -> None:
                srv = await asyncio.start_server(handle, "127.0.0.1", 0)
                self.port = srv.sockets[0].getsockname()[1]
                started.set()

            self._loop.run_until_complete(main())
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        started.wait(5)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


def _get(port: int, request: bytes) -> tuple[bytes, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(request)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return head, body


def _req(method: str = "GET", target: str = "/metrics") -> bytes:
    return f"{method} {target} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode()


@pytest.fixture()
def rig() -> Iterator[tuple[_Upstream, dict[str, int | None], MetricsPassthrough]]:
    up = _Upstream(BIG)
    source: dict[str, int | None] = {"port": up.port}
    server = MetricsPassthrough(lambda: source["port"], port=0, bind_hosts=("127.0.0.1",))
    server.start()
    try:
        yield up, source, server
    finally:
        server.stop()
        up.close()


def test_a_large_metrics_page_streams_through_unchanged(rig) -> None:  # noqa: ANN001
    up, _, server = rig
    head, body = _get(server.port, _req())
    assert head.startswith(b"HTTP/1.1 200")
    assert body == BIG
    # What the worker saw was built by the agent, not by the client.
    assert up.requests[0].startswith(b"GET /metrics HTTP/1.1\r\nHost: 127.0.0.1:")


def test_a_query_string_is_still_metrics_and_is_not_forwarded(rig) -> None:  # noqa: ANN001
    up, _, server = rig
    head, _ = _get(server.port, _req(target="/metrics?x=1"))
    assert head.startswith(b"HTTP/1.1 200")
    assert b"x=1" not in up.requests[-1]


@pytest.mark.parametrize("target", ["/v1/models", "/health", "/metrics/", "/metrics/x", "/", "//metrics", "/v1/chat/completions"])
def test_every_other_path_is_refused_without_touching_the_worker(rig, target: str) -> None:  # noqa: ANN001
    up, _, server = rig
    head, _ = _get(server.port, _req(target=target))
    assert head.startswith(b"HTTP/1.1 403")
    assert up.requests == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
def test_every_other_method_is_refused(rig, method: str) -> None:  # noqa: ANN001
    up, _, server = rig
    head, _ = _get(server.port, _req(method=method))
    assert head.startswith(b"HTTP/1.1 405") and b"Allow: GET" in head
    assert up.requests == []


def test_a_garbled_request_is_refused(rig) -> None:  # noqa: ANN001
    _, _, server = rig
    head, _ = _get(server.port, b"nonsense\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 400")


def test_no_worker_is_503(rig) -> None:  # noqa: ANN001
    _, source, server = rig
    source["port"] = None
    head, _ = _get(server.port, _req())
    assert head.startswith(b"HTTP/1.1 503")


def test_a_dead_worker_is_503(rig) -> None:  # noqa: ANN001
    up, source, server = rig
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        source["port"] = s.getsockname()[1]  # nothing listens here once it is closed
    head, _ = _get(server.port, _req())
    assert head.startswith(b"HTTP/1.1 503")


def test_the_default_port_clears_the_proxy_ranges() -> None:
    from faden_agent.config import AgentConfig  # noqa: F401

    assert not 8100 <= DEFAULT_PORT < 9000 and not 28100 <= DEFAULT_PORT < 29000


def test_worker_port_is_the_ready_workers() -> None:
    class M:
        def __init__(self, records: list[dict]) -> None:
            self.r = records

        def _records(self) -> list[dict]:
            return self.r

        def _leader(self, record: dict) -> object | None:
            return None if record.get("dead") else object()

    assert worker_port(M([])) is None
    assert worker_port(M([{"state": "starting", "port": 28100}])) is None
    assert worker_port(M([{"state": "ready", "port": 28100, "dead": True}])) is None
    assert worker_port(M([{"state": "stopped", "port": 1}, {"state": "ready", "port": 28101}])) == 28101


def _service(native: object | None, port: int):  # noqa: ANN202
    from types import SimpleNamespace

    from faden_agent.service import NodeAgentService

    svc = NodeAgentService.__new__(NodeAgentService)
    svc._native = native  # noqa: SLF001
    svc._metrics_passthrough = None  # noqa: SLF001
    svc._config = SimpleNamespace(native_metrics_port=port)  # noqa: SLF001
    return svc


def test_linux_never_advertises_native_metrics() -> None:
    caps: dict = {}
    _service(None, 9468)._start_native_metrics(caps)  # noqa: SLF001
    assert "native_metrics" not in caps


def test_a_mac_advertises_the_bound_port_and_switch_off_is_honoured() -> None:
    class Native:
        def _records(self) -> list[dict]:
            return []

    caps: dict = {}
    svc = _service(Native(), 0)
    svc._start_native_metrics(caps)  # noqa: SLF001
    assert caps == {}, "0 turns it off"

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    svc = _service(Native(), free)
    svc._start_native_metrics(caps)  # noqa: SLF001
    try:
        assert caps == {"native_metrics": {"port": free}}
        head, _ = _get(free, _req())
        assert head.startswith(b"HTTP/1.1 503"), "no worker running"
    finally:
        svc._metrics_passthrough.stop()  # noqa: SLF001
