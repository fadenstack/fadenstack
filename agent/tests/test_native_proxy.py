"""B1: the Mac's mTLS proxy -- real certificates, real sockets, a scripted upstream."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from faden_agent import model_tls, pki_util
from faden_agent.model_tls import ModelTls, ModelTlsError, NativeBackend
from faden_agent.native_proxy import NativeProxy, ProxyLimits, build_identity, self_test
from faden_agent.service import add_agent_capabilities, add_native_proxy_capabilities

NAME = "fadenstack-gateway"


class Pki:
    def __init__(self, root: Path) -> None:
        self.dir = root
        ca_key, self.ca = pki_util.make_ca()
        self.ca_key = ca_key
        self._write("ca", None, self.ca)
        self.server = self.leaf("server", "10.0.0.1", ips=("127.0.0.1",), eku=(pki_util.SERVER_AUTH,))
        self.server2 = self.leaf("server2", "10.0.0.1", ips=("127.0.0.1",), eku=(pki_util.SERVER_AUTH,))
        self.gateway = self.leaf("gateway", NAME)
        self.wrong_cn = self.leaf("wrong_cn", "somebody")
        self.server_only = self.leaf("server_only", NAME, eku=(pki_util.SERVER_AUTH,))
        self.no_eku = self.leaf("no_eku", NAME, eku=None)
        now = datetime.now(UTC)
        self.expired = self.leaf("expired", NAME, not_before=now - timedelta(days=3), not_after=now - timedelta(days=2))
        other_key, other_ca = pki_util.make_ca("Another CA")
        k, c = pki_util.issue(other_key, other_ca, NAME)
        self._write("other_ca_gateway", k, c)
        self.other_ca_gateway = ("other_ca_gateway.crt", "other_ca_gateway.key")

    def _write(self, name: str, key: Any, cert: Any) -> None:
        (self.dir / f"{name}.crt").write_bytes(pki_util.cert_pem(cert))
        if key is not None:
            (self.dir / f"{name}.key").write_bytes(pki_util.key_pem(key))

    def leaf(self, name: str, cn: str, **kw: Any) -> tuple[str, str]:
        key, cert = pki_util.issue(self.ca_key, self.ca, cn, **kw)
        self._write(name, key, cert)
        return f"{name}.crt", f"{name}.key"

    def path(self, name: str) -> Path:
        return self.dir / name

    def identity(self, server: tuple[str, str] | None = None) -> Any:
        s = server or self.server
        return build_identity(self.path(s[0]), self.path(s[1]), self.path("ca.crt"), NAME)


@pytest.fixture(scope="module")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    return Pki(tmp_path_factory.mktemp("pki"))


class Upstream:
    """A scripted loopback server on its own thread; ``script(reader, writer, head, body)``."""

    def __init__(self, script: Any) -> None:
        self.script = script
        self.requests: list[tuple[bytes, bytes]] = []
        self.closed_early = threading.Event()
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        self.port = 0

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(5)

    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        low = head.lower()
        body = b""
        if b"content-length:" in low:
            n = int(low.split(b"content-length:")[1].split(b"\r\n")[0])
            body = await r.readexactly(n)
        elif b"transfer-encoding: chunked" in low:
            while True:
                size = int((await r.readline()).strip(), 16)
                body += await r.readexactly(size + 2)
                if size == 0:
                    break
        self.requests.append((head, body))
        try:
            await self.script(r, w, head, body)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            w.close()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


async def _ok(r: Any, w: Any, head: bytes, body: bytes) -> None:
    out = json.dumps({"len": len(body)}).encode()
    w.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: %d\r\nconnection: close\r\n\r\n" % len(out) + out)
    await w.drain()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Rig:
    def __init__(self, pki: Pki, upstream: Upstream, **limits: Any) -> None:
        self.pki = pki
        self.upstream = upstream
        limits.setdefault("drain_sec", 2.0)
        self.proxy = NativeProxy(ProxyLimits(**limits), bind_hosts=("127.0.0.1",))
        self.proxy.set_identity(pki.identity())
        self.port = free_port()
        self.proxy.start()
        self.proxy._submit(self.proxy._apply({self.port: upstream.port})).result(5)  # noqa: SLF001

    def connect(self, client: tuple[str, str] | None = None, *, ca: str = "ca.crt") -> ssl.SSLSocket:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cafile=str(self.pki.path(ca)))
        ctx.check_hostname = False
        if client:
            ctx.load_cert_chain(str(self.pki.path(client[0])), str(self.pki.path(client[1])))
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        return ctx.wrap_socket(raw, server_hostname="127.0.0.1")

    def exchange(self, request: bytes, client: tuple[str, str] | None = None, *, timeout: float = 10) -> bytes:
        gateway = client or self.pki.gateway
        try:
            tls = self.connect(gateway)
            tls.settimeout(timeout)
            tls.sendall(request)
            data = b""
            while True:
                try:
                    chunk = tls.recv(65536)
                except (ssl.SSLError, ConnectionError, TimeoutError):
                    break
                if not chunk:
                    break
                data += chunk
            tls.close()
            return data
        except (ssl.SSLError, ConnectionError, OSError):
            return b""

    def close(self) -> None:
        self.proxy.stop()
        self.upstream.stop()


@pytest.fixture
def rig_factory(pki: Pki) -> Any:
    rigs: list[Rig] = []

    def make(script: Any = _ok, **limits: Any) -> Rig:
        r = Rig(pki, Upstream(script), **limits)
        rigs.append(r)
        return r

    yield make
    for r in rigs:
        r.close()


def status(data: bytes) -> int:
    return int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else 0


def post(path: str, body: bytes, extra: str = "") -> bytes:
    return (f"POST {path} HTTP/1.1\r\nHost: node\r\nContent-Length: {len(body)}\r\n{extra}Connection: close\r\n\r\n").encode() + body


GET_HEALTH = b"GET /health HTTP/1.1\r\nHost: node\r\nConnection: close\r\n\r\n"


# ── mTLS ────────────────────────────────────────────────────────────────


def test_the_gateway_reaches_the_model(rig_factory: Any) -> None:
    rig = rig_factory()
    assert status(rig.exchange(GET_HEALTH)) == 200
    data = rig.exchange(post("/v1/chat/completions", b'{"a":1}'))
    assert status(data) == 200 and b'"len": 7' in data


@pytest.mark.parametrize("name", ["wrong_cn", "server_only", "no_eku", "expired", "other_ca_gateway"])
def test_other_certificates_never_get_a_response(rig_factory: Any, pki: Pki, name: str) -> None:
    rig = rig_factory()
    cert = getattr(pki, name)
    assert rig.exchange(GET_HEALTH, cert) == b""
    assert rig.upstream.requests == []


def test_no_certificate_never_gets_a_response(rig_factory: Any) -> None:
    rig = rig_factory()
    try:
        tls = rig.connect(None)
        tls.sendall(GET_HEALTH)
        data = tls.recv(4096)
    except (ssl.SSLError, ConnectionError, OSError):
        data = b""
    assert data == b"" and rig.upstream.requests == []


def test_a_plain_text_client_is_dropped(rig_factory: Any) -> None:
    rig = rig_factory()
    with socket.create_connection(("127.0.0.1", rig.port), timeout=5) as s:
        s.sendall(GET_HEALTH)
        s.settimeout(5)
        try:
            data = s.recv(4096)
        except (ConnectionError, TimeoutError):
            data = b""
    assert b"200" not in data and rig.upstream.requests == []


# ── the allowlist and the headers ────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "want"), [
    ("GET", "/health", 200), ("GET", "/v1/models", 200), ("GET", "/health?x=1", 200),
    ("POST", "/v1/chat/completions", 200), ("POST", "/v1/completions", 200), ("POST", "/v1/embeddings", 200),
    ("POST", "/v1/rerank", 200), ("POST", "/v1/score", 200),
    ("POST", "/v1/models", 405), ("GET", "/v1/chat/completions", 405), ("DELETE", "/health", 405),
    ("GET", "/metrics", 403), ("GET", "/v1/chat/completions/", 403), ("POST", "/tokenize", 403),
    ("POST", "/v1/load_lora_adapter", 403), ("POST", "/sleep", 403), ("GET", "/v1/../metrics", 403),
    ("GET", "//health", 403),
])
def test_the_allowlist(rig_factory: Any, method: str, path: str, want: int) -> None:
    rig = rig_factory()
    body = b"{}" if method == "POST" else b""
    req = f"{method} {path} HTTP/1.1\r\nHost: node\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    assert status(rig.exchange(req)) == want
    if want != 200:
        assert rig.upstream.requests == []


def test_forwarded_headers_are_set_here_not_taken_from_the_client(rig_factory: Any) -> None:
    rig = rig_factory()
    req = (b"GET /health HTTP/1.1\r\nHost: evil.example\r\nX-Forwarded-For: 6.6.6.6\r\nX-Real-IP: 6.6.6.6\r\n"
           b"Forwarded: for=6.6.6.6\r\nX-Forwarded-Proto: http\r\nX-Remote-User: root\r\nUpgrade: websocket\r\n"
           b"Connection: close, X-Secret\r\nX-Secret: s\r\nAuthorization: Bearer k\r\n\r\n")
    assert status(rig.exchange(req)) == 200
    head = rig.upstream.requests[0][0].lower()
    assert b"host: 127.0.0.1:%d" % rig.upstream.port in head
    assert b"x-forwarded-for: 127.0.0.1\r\n" in head and b"6.6.6.6" not in head
    assert b"x-forwarded-proto: https" in head and b"x-forwarded-proto: http\r" not in head
    for gone in (b"x-real-ip", b"forwarded: ", b"x-remote-user", b"upgrade", b"x-secret"):
        assert gone not in head
    assert b"authorization: bearer k" in head


# ── framing ─────────────────────────────────────────────────────────────


def test_cl_and_te_together_are_refused(rig_factory: Any) -> None:
    rig = rig_factory()
    req = (b"POST /v1/completions HTTP/1.1\r\nHost: n\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n"
           b"\r\n0\r\n\r\nGET /metrics HTTP/1.1\r\nHost: n\r\n\r\n")
    assert status(rig.exchange(req)) == 400 and rig.upstream.requests == []


def test_duplicate_content_length_is_refused(rig_factory: Any) -> None:
    rig = rig_factory()
    req = b"POST /v1/completions HTTP/1.1\r\nHost: n\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    assert status(rig.exchange(req)) == 400 and rig.upstream.requests == []


def test_http_1_0_and_a_garbled_request_are_refused(rig_factory: Any) -> None:
    rig = rig_factory()
    assert status(rig.exchange(b"GET /health HTTP/1.0\r\n\r\n")) in (400, 505)
    assert status(rig.exchange(b"GARBAGE\r\n\r\n")) == 400
    assert rig.upstream.requests == []


def test_a_chunked_request_goes_through(rig_factory: Any) -> None:
    rig = rig_factory()
    req = b"POST /v1/completions HTTP/1.1\r\nHost: n\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n3\r\nabc\r\n0\r\n\r\n"
    data = rig.exchange(req)
    assert status(data) == 200 and b"abc" in rig.upstream.requests[0][1]


def test_too_many_and_too_big_headers(rig_factory: Any) -> None:
    rig = rig_factory(max_headers=10, max_header_bytes=2048)
    many = "".join(f"X-{i}: v\r\n" for i in range(30))
    assert status(rig.exchange(f"GET /health HTTP/1.1\r\nHost: n\r\n{many}\r\n".encode())) in (400, 431)
    big = f"GET /health HTTP/1.1\r\nHost: n\r\nX-Big: {'a' * 5000}\r\n\r\n".encode()
    assert status(rig.exchange(big)) in (400, 431)
    assert rig.upstream.requests == []


# ── bodies ──────────────────────────────────────────────────────────────


def test_a_big_body_passes_and_one_over_the_cap_does_not(rig_factory: Any) -> None:
    rig = rig_factory(max_body_bytes=8 * 1024 * 1024)
    body = b"x" * (6 * 1024 * 1024)
    data = rig.exchange(post("/v1/chat/completions", body), timeout=30)
    assert status(data) == 200 and f'"len": {len(body)}'.encode() in data
    n = len(rig.upstream.requests)
    over = rig.exchange(
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: n\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (9 * 1024 * 1024),
    )
    assert status(over) == 413 and len(rig.upstream.requests) == n


def test_a_chunked_body_over_the_cap_is_cut(rig_factory: Any) -> None:
    rig = rig_factory(max_body_bytes=1024 * 1024)
    tls = rig.connect(rig.pki.gateway)
    tls.sendall(b"POST /v1/completions HTTP/1.1\r\nHost: n\r\nTransfer-Encoding: chunked\r\n\r\n")
    chunk = b"%x\r\n" % 65536 + b"x" * 65536 + b"\r\n"
    got = b""
    try:
        for _ in range(60):
            tls.sendall(chunk)
        tls.settimeout(5)
        got = tls.recv(4096)
    except (ssl.SSLError, ConnectionError, OSError):
        pass
    assert got == b"" or status(got) == 413


# ── streaming ───────────────────────────────────────────────────────────


async def _sse(r: Any, w: Any, head: bytes, body: bytes) -> None:
    w.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\nconnection: close\r\n\r\n")
    for i in range(3):
        ev = f"data: {i}\n\n".encode()
        w.write(b"%x\r\n" % len(ev) + ev + b"\r\n")
        await w.drain()
        await asyncio.sleep(0.6)
    w.write(b"0\r\n\r\n")
    await w.drain()


def test_sse_arrives_event_by_event(rig_factory: Any) -> None:
    rig = rig_factory(_sse)
    tls = rig.connect(rig.pki.gateway)
    tls.sendall(post("/v1/chat/completions", b"{}"))
    tls.settimeout(10)
    start = time.monotonic()
    data = b""
    while b"data: 0" not in data:
        data += tls.recv(4096)
    assert time.monotonic() - start < 1.0  # the first event, not the whole stream
    while not data.endswith(b"0\r\n\r\n"):
        data += tls.recv(4096)
    assert b"data: 2" in data and b"text/event-stream" in data


SENT = {"n": 0}


async def _flood(r: Any, w: Any, head: bytes, body: bytes) -> None:
    SENT["n"] = 0
    total, piece = 64 * 1024 * 1024, b"y" * 65536
    w.write(b"HTTP/1.1 200 OK\r\ncontent-length: %d\r\nconnection: close\r\n\r\n" % total)
    while SENT["n"] < total:
        w.write(piece)
        await w.drain()
        SENT["n"] += len(piece)


def test_a_slow_reader_holds_the_upstream_back(rig_factory: Any) -> None:
    rig = rig_factory(_flood)
    tls = rig.connect(rig.pki.gateway)
    tls.sendall(post("/v1/chat/completions", b"{}"))
    time.sleep(2.0)  # the client reads nothing
    assert SENT["n"] < 32 * 1024 * 1024, "the proxy buffered the response instead of pushing back"
    tls.settimeout(30)
    got = 0
    while got < 64 * 1024 * 1024:
        chunk = tls.recv(1 << 20)
        if not chunk:
            break
        got += len(chunk)
    assert got >= 64 * 1024 * 1024


async def _hang(r: Any, w: Any, head: bytes, body: bytes) -> None:
    w.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\ndata: 0\n\n")
    await w.drain()
    try:
        while True:
            await asyncio.sleep(0.1)
            w.write(b"data: x\n\n")
            await w.drain()
    except ConnectionError:
        rig_closed.set()


rig_closed = threading.Event()


def test_a_client_that_leaves_closes_the_upstream(rig_factory: Any) -> None:
    rig_closed.clear()
    rig = rig_factory(_hang)
    tls = rig.connect(rig.pki.gateway)
    tls.sendall(post("/v1/chat/completions", b"{}"))
    tls.settimeout(5)
    got = b""
    while b"data: 0" not in got:
        got += tls.recv(4096)
    tls.close()
    assert rig_closed.wait(10), "vLLM would go on generating for nobody"


# ── timeouts and limits ─────────────────────────────────────────────────


def test_slowloris_headers_time_out(rig_factory: Any) -> None:
    rig = rig_factory(header_timeout=1.0)
    tls = rig.connect(rig.pki.gateway)
    tls.sendall(b"GET /health HTTP/1.1\r\nHost: n\r\n")
    tls.settimeout(8)
    start = time.monotonic()
    try:
        for _ in range(8):  # a byte at a time keeps a per-read timeout alive; a total one does not
            time.sleep(0.4)
            tls.sendall(b"X: y\r\n")
    except (ssl.SSLError, ConnectionError, OSError):
        pass
    assert time.monotonic() - start < 6
    assert rig.upstream.requests == []


def test_an_idle_connection_is_closed(rig_factory: Any) -> None:
    rig = rig_factory(header_timeout=1.0, idle_timeout=1.0)
    tls = rig.connect(rig.pki.gateway)
    tls.settimeout(6)
    assert tls.recv(10) == b""  # closed by the proxy, within the timeout


def test_a_dead_upstream_is_502_and_a_silent_one_504(rig_factory: Any) -> None:
    rig = rig_factory(connect_timeout=1.0, upstream_read_timeout=1.0)
    rig.proxy._submit(rig.proxy._apply({rig.port: free_port()})).result(5)  # noqa: SLF001
    assert status(rig.exchange(GET_HEALTH)) == 502

    async def silent(r: Any, w: Any, head: bytes, body: bytes) -> None:
        await asyncio.sleep(5)

    rig2 = rig_factory(silent, upstream_read_timeout=1.0)
    assert status(rig2.exchange(GET_HEALTH)) == 504


def test_connections_are_capped(rig_factory: Any) -> None:
    rig = rig_factory(max_connections=2)
    held = [rig.connect(rig.pki.gateway) for _ in range(2)]
    time.sleep(0.3)
    assert rig.exchange(GET_HEALTH) == b""
    for h in held:
        h.close()
    time.sleep(0.5)
    assert status(rig.exchange(GET_HEALTH)) == 200


# ── rotation ────────────────────────────────────────────────────────────


def test_a_new_certificate_does_not_drop_an_open_stream(rig_factory: Any, pki: Pki) -> None:
    rig = rig_factory(_sse)
    tls = rig.connect(rig.pki.gateway)
    first = tls.getpeercert(binary_form=True)
    tls.sendall(post("/v1/chat/completions", b"{}"))
    tls.settimeout(10)
    data = b""
    while b"data: 0" not in data:
        data += tls.recv(4096)
    rig.proxy.set_identity(pki.identity(pki.server2))  # renewal mid-stream
    while not data.endswith(b"0\r\n\r\n"):
        data += tls.recv(4096)
    assert b"data: 2" in data
    fresh = rig.connect(rig.pki.gateway)
    assert fresh.getpeercert(binary_form=True) != first


# ── the self-test and the capabilities ──────────────────────────────────


def test_the_self_test_passes_here() -> None:
    assert self_test() is None


def test_capabilities_wait_for_the_self_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    caps: dict[str, Any] = {"container_runtime": "docker", "docker_available": True}
    add_agent_capabilities(caps)
    assert "model_tls" not in caps and "model_tls_native" not in caps
    for gone in ("ray_tls", "ray_drain", "serve_scale", "container_runtime"):
        assert gone not in caps
    add_native_proxy_capabilities(caps, "boom")
    assert "model_tls" not in caps
    add_native_proxy_capabilities(caps, None)
    assert caps["model_tls"] == 1 and caps["model_tls_native"] == 1


# ── routes with owners ──────────────────────────────────────────────────


def _mac_tls(pki: Pki, tmp_path: Path) -> ModelTls:
    d = tmp_path / "model-tls"
    d.mkdir()
    (d / model_tls.CERT_FILE).write_bytes(pki.path("server.crt").read_bytes())
    (d / model_tls.KEY_FILE).write_bytes(pki.path("server.key").read_bytes())
    (d / model_tls.TRUST_FILE).write_bytes(pki.path("ca.crt").read_bytes())
    tls = ModelTls(runtime=None, directory=d)
    backend = NativeBackend(tls, NativeProxy(ProxyLimits(), bind_hosts=("127.0.0.1",)), firewall=lambda: {"state": "on", "detail": "x"})
    tls._backend = backend  # noqa: SLF001
    return tls


@pytest.mark.asyncio
async def test_owned_routes(pki: Pki, tmp_path: Path) -> None:
    tls = _mac_tls(pki, tmp_path)
    up = Upstream(_ok)
    p = free_port()
    try:
        res = await tls.ensure_route(p, up.port, owner="dep-1", generation=1)
        assert res == {"protected": True, "public_port": p, "internal_port": up.port}
        with pytest.raises(ModelTlsError):
            await tls.ensure_route(p, up.port, owner="dep-2", generation=1)
        with pytest.raises(ModelTlsError):
            await tls.ensure_route(p, up.port)  # an owned port is not taken without an owner
        assert (await tls.ensure_route(p, up.port, owner="dep-1", generation=2))["protected"]
        with pytest.raises(ModelTlsError):
            await tls.ensure_route(p, up.port, owner="dep-1", generation=1)  # stale
        stale = await tls.remove_route(p, owner="dep-1", generation=1)
        assert stale["removed"] is False and tls.routes()
        other = await tls.remove_route(p, owner="dep-2")
        assert other["removed"] is False and tls.routes()
        assert (await tls.remove_route(p, owner="dep-1"))["removed"] is True
        again = await tls.remove_route(p, owner="dep-1")
        assert again["removed"] is False and not tls.routes() and tls.owners() == {}
        assert tls.status()["proxy"]["backend"] == "native"
        assert (await tls._backend.check_firewall())["state"] == "on"  # noqa: SLF001
    finally:
        tls._backend.proxy.stop()  # noqa: SLF001
        up.stop()


@pytest.mark.asyncio
async def test_a_route_without_owner_is_0_2_12(pki: Pki, tmp_path: Path) -> None:
    tls = _mac_tls(pki, tmp_path)
    up = Upstream(_ok)
    p = free_port()
    try:
        assert await tls.ensure_route(p, up.port) == {"protected": True, "public_port": p, "internal_port": up.port}
        assert not (tmp_path / "model-tls" / model_tls.OWNERS_FILE).exists()
        assert json.loads((tmp_path / "model-tls" / model_tls.ROUTES_FILE).read_text()) == {str(p): up.port}
        await tls.remove_route(p)
        await tls.remove_route(p)  # idempotent
        assert tls.routes() == {}
    finally:
        tls._backend.proxy.stop()  # noqa: SLF001
        up.stop()


@pytest.mark.asyncio
async def test_a_taken_port_is_refused_and_not_kept(pki: Pki, tmp_path: Path) -> None:
    tls = _mac_tls(pki, tmp_path)
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))  # noqa: S104
        s.listen()
        with pytest.raises(ModelTlsError):
            await tls.ensure_route(s.getsockname()[1], 9, owner="d", generation=1)
    assert tls.routes() == {} and tls.owners() == {}


@pytest.mark.asyncio
async def test_stored_routes_are_served_again_after_a_restart(pki: Pki, tmp_path: Path) -> None:
    tls = _mac_tls(pki, tmp_path)
    up = Upstream(_ok)
    p = free_port()
    try:
        await tls.ensure_route(p, up.port, owner="d", generation=1)
        tls._backend.proxy.stop()  # noqa: SLF001
        fresh = ModelTls(runtime=None, directory=tmp_path / "model-tls")
        fresh._backend = NativeBackend(fresh, NativeProxy(ProxyLimits(), bind_hosts=("127.0.0.1",)))  # noqa: SLF001
        await fresh.restore()
        assert p in fresh._backend.proxy.bound_ports()  # noqa: SLF001
        fresh._backend.proxy.stop()  # noqa: SLF001
    finally:
        up.stop()


def test_firewall_and_san_drift(pki: Pki, tmp_path: Path) -> None:
    assert model_tls.firewall_state(lambda: "Firewall is enabled. (State = 1)")["state"] == "on"
    assert model_tls.firewall_state(lambda: "Firewall is disabled. (State = 0)")["state"] == "off"

    def boom() -> str:
        raise OSError("no such tool")

    assert model_tls.firewall_state(boom)["state"] == "unknown"
