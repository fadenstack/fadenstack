"""The Mac's model-TLS proxy: an asyncio mutual-TLS reverse proxy in front of loopback vLLM servers.

It replaces Linux's nginx container on a Mac (``model_tls.py`` chooses). It runs
on its OWN event loop in a dedicated thread, so nothing else the agent does
(collectors, the node stream, model sync, the native manager) can stall model
traffic.

Order of events for a connection:

1. accept (counted against ``max_connections``);
2. the TLS handshake, with a client certificate required, chained to the
   machine CA (the context's verification; its purpose check requires
   clientAuth when an EKU is present);
3. before any HTTP byte is parsed: the certificate must be directly issued by
   a CA we trust, carry EKU clientAuth, and its CN must equal ``client_name``;
4. h11 parses the request; framing that could smuggle (CL and TE together,
   duplicate CL, HTTP/1.0) is refused; the path and method must be on the
   allowlist;
5. the request is forwarded to ``127.0.0.1:<internal>`` with Host and
   X-Forwarded-* set here (never taken from the client), and the response is
   streamed back with backpressure. A client that goes away closes the
   upstream socket, so vLLM aborts the generation.

New handshakes use the identity current when they start (:meth:`set_identity`
swaps it atomically); open connections keep theirs, so a certificate renewal
drops no stream. Stopping drains for at most ``drain_sec``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import socket
import ssl
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import h11

log = logging.getLogger(__name__)

#: Exact path -> methods. Anything else is refused (403 unknown path, 405 wrong method).
ALLOWLIST: dict[str, frozenset[str]] = {
    "/health": frozenset({"GET"}),
    "/v1/models": frozenset({"GET"}),
    "/v1/chat/completions": frozenset({"POST"}),
    "/v1/completions": frozenset({"POST"}),
    "/v1/embeddings": frozenset({"POST"}),
    "/v1/rerank": frozenset({"POST"}),
    "/v1/score": frozenset({"POST"}),
}

_HOP_BY_HOP = frozenset({
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization", b"proxy-connection",
    b"te", b"trailer", b"trailers", b"transfer-encoding", b"upgrade",
})
#: Dropped from the request: what the proxy sets itself, and every client-identity claim.
_NEVER_FORWARD = frozenset({
    b"host", b"expect", b"forwarded", b"x-real-ip", b"x-client-ip", b"x-remote-addr", b"x-remote-user",
    b"x-original-forwarded-for", b"true-client-ip", b"cf-connecting-ip", b"x-ssl-client-cert",
    b"x-ssl-client-dn", b"x-client-cert", b"x-client-dn",
})


class ProxyError(RuntimeError):
    """The proxy could not do what was asked (a port taken, no identity)."""


@dataclass
class ProxyLimits:
    """Every bound the proxy keeps; each is settable as ``FADEN_NODE_AGENT_PROXY_<NAME>``."""

    max_header_bytes: int = 32 * 1024
    max_headers: int = 100
    max_body_bytes: int = 32 * 1024 * 1024
    handshake_timeout: float = 10.0
    header_timeout: float = 30.0
    idle_timeout: float = 75.0
    body_timeout: float = 75.0
    write_timeout: float = 120.0
    connect_timeout: float = 5.0
    upstream_read_timeout: float = 3600.0
    max_connections: int = 256
    drain_sec: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProxyLimits:
        env = os.environ if env is None else env
        out = cls()
        for f in fields(cls):
            raw = env.get("FADEN_NODE_AGENT_PROXY_" + f.name.upper(), "").strip()
            if not raw:
                continue
            try:
                setattr(out, f.name, type(getattr(out, f.name))(raw))
            except ValueError:
                log.warning("Ignoring FADEN_NODE_AGENT_PROXY_%s=%r", f.name.upper(), raw)
        return out


@dataclass(frozen=True)
class Identity:
    """What new handshakes use: the server certificate, the trusted CAs, the one client name let through."""

    context: ssl.SSLContext
    client_name: str
    cas: tuple[Any, ...]
    fingerprint: str


def build_identity(cert: Path, key: Path, trust: Path, client_name: str) -> Identity:
    from cryptography import x509  # noqa: PLC0415

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    ctx.load_verify_locations(cafile=str(trust))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_alpn_protocols(["http/1.1"])
    trust_bytes = trust.read_bytes()
    cas = tuple(x509.load_pem_x509_certificates(trust_bytes))
    if not cas:
        raise ProxyError("The trust file holds no certificate.")
    digest = hashlib.sha256()
    for part in (cert.read_bytes(), key.read_bytes(), trust_bytes, client_name.encode()):
        digest.update(hashlib.sha256(part).digest())
    return Identity(ctx, client_name, cas, digest.hexdigest())


def client_acceptable(identity: Identity, der: bytes | None) -> str | None:
    """``None`` when the peer's certificate is the gateway's, else why not."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: PLC0415

    if not der:
        return "no client certificate"
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        return "unreadable client certificate"
    cns = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if cns != [identity.client_name]:
        return "client certificate name is not the gateway's"
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return "client certificate has no extended key usage"
    if ExtendedKeyUsageOID.CLIENT_AUTH not in eku:
        return "client certificate is not for client use"
    for ca in identity.cas:
        try:
            cert.verify_directly_issued_by(ca)
            return None
        except Exception:  # noqa: BLE001, S112 - try the next CA
            continue
    return "client certificate is not issued directly by the machine CA"


class _Protocol(asyncio.StreamReaderProtocol):
    """Reading is paused from the first moment, so no ClientHello byte lands in the stream buffer before TLS starts."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.pause_reading()  # type: ignore[attr-defined]
        super().connection_made(transport)


class _Refused(Exception):
    def __init__(self, status: int, message: str, headers: list[tuple[str, str]] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or []


class _Gone(Exception):
    """The client went away."""


@dataclass
class _Listener:
    internal: int
    servers: list[asyncio.AbstractServer] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    port: int = 0


class NativeProxy:
    """The proxy: one listener per public port, on its own thread and loop."""

    def __init__(self, limits: ProxyLimits | None = None, *, bind_hosts: tuple[str, ...] = ("0.0.0.0", "::")) -> None:  # noqa: S104
        self.limits = limits or ProxyLimits.from_env()
        self._bind_hosts = bind_hosts
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._listeners: dict[int, _Listener] = {}
        self._identity: Identity | None = None
        self._active = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()

    # ── thread ──────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="faden-model-proxy", daemon=True)
            self._thread.start()
        self._ready.wait(5)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def _submit(self, coro: Any) -> Future[Any]:
        self.start()
        assert self._loop is not None  # noqa: S101
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── identity ────────────────────────────────────────────────────

    def set_identity(self, identity: Identity) -> None:
        """New handshakes use *identity*; open connections keep theirs."""
        self._identity = identity

    @property
    def identity(self) -> Identity | None:
        return self._identity

    # ── routes ──────────────────────────────────────────────────────

    def bound_ports(self) -> list[int]:
        return sorted(self._listeners)

    def connections(self) -> int:
        return self._active

    async def apply(self, routes: Mapping[int, int]) -> None:
        """Serve exactly *routes* (public -> internal): bind new ports first, then drop the gone ones."""
        await asyncio.wrap_future(self._submit(self._apply(dict(routes))))

    async def _bind(self, port: int) -> _Listener:
        assert self._loop is not None  # noqa: S101
        listener = _Listener(internal=0)
        socks: list[socket.socket] = []
        try:
            for host in self._bind_hosts:
                family = socket.AF_INET6 if ":" in host else socket.AF_INET
                try:
                    sock = socket.socket(family, socket.SOCK_STREAM)
                except OSError:
                    if family == socket.AF_INET6:
                        continue  # no IPv6 here
                    raise
                socks.append(sock)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.setblocking(False)
                try:
                    sock.bind((host, port))
                except OSError as exc:
                    if family == socket.AF_INET6 and port == 0:
                        socks.pop()
                        sock.close()
                        continue
                    raise ProxyError(f"Port {port} cannot be bound on {host}: {exc.strerror or exc}") from exc
                if port == 0 and family == socket.AF_INET:
                    port = sock.getsockname()[1]
                sock.listen(128)
            for sock in socks:
                server = await self._loop.create_server(lambda p=port: self._factory(p), sock=sock)
                listener.servers.append(server)
        except BaseException:
            for server in listener.servers:
                server.close()
            for sock in socks:
                sock.close()
            raise
        listener.port = port
        return listener

    async def _apply(self, routes: dict[int, int]) -> None:
        new: dict[int, _Listener] = {}
        try:
            for public, internal in routes.items():
                if public in self._listeners:
                    self._listeners[public].internal = internal
                    continue
                listener = await self._bind(public)
                listener.internal = internal
                new[public] = listener
        except BaseException:
            for listener in new.values():
                await self._close_listener(listener, drain=0)
            raise
        self._listeners.update(new)
        for public in [p for p in self._listeners if p not in routes]:
            gone = self._listeners.pop(public)
            # Accepting stops now; open streams get up to drain_sec.
            asyncio.ensure_future(self._close_listener(gone, drain=self.limits.drain_sec))  # noqa: RUF006

    async def _close_listener(self, listener: _Listener, *, drain: float) -> None:
        for server in listener.servers:
            server.close()
        tasks = [t for t in listener.tasks if not t.done()]
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=drain)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2)

    def stop(self) -> None:
        """Close every listener, let streams finish for ``drain_sec``, end the thread."""
        if not self.running():
            return
        try:
            self._submit(self._stop()).result(self.limits.drain_sec + 10)
        except Exception:  # noqa: BLE001
            log.exception("Model proxy: stop did not finish cleanly")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(5)

    async def _stop(self) -> None:
        listeners = list(self._listeners.values())
        self._listeners.clear()
        await asyncio.gather(*(self._close_listener(x, drain=self.limits.drain_sec) for x in listeners))

    # ── connections ─────────────────────────────────────────────────

    def _factory(self, port: int) -> asyncio.StreamReaderProtocol:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=2**16, loop=loop)

        async def serve(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            listener = self._listeners.get(port)
            task = asyncio.current_task()
            if listener is not None and task is not None:
                listener.tasks.add(task)
            self._active += 1
            try:
                if self._active > self.limits.max_connections:
                    w.transport.abort()
                    return
                await self._connection(r, w, listener.internal if listener else 0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.debug("Model proxy: connection ended with an error", exc_info=True)
            finally:
                self._active -= 1
                if listener is not None and task is not None:
                    listener.tasks.discard(task)
                with contextlib.suppress(Exception):
                    w.close()

        return _Protocol(reader, serve, loop)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, internal: int) -> None:
        lim = self.limits
        identity = self._identity
        if identity is None:
            writer.transport.abort()
            return
        sock = writer.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = writer.get_extra_info("peername") or ("?", 0)
        try:
            await writer.start_tls(identity.context, ssl_handshake_timeout=lim.handshake_timeout)
        except (ssl.SSLError, OSError, TimeoutError, asyncio.TimeoutError) as exc:
            log.debug("Model proxy: handshake from %s refused: %s", peer[0], exc)
            writer.transport.abort()
            return
        ssl_obj = writer.get_extra_info("ssl_object")
        why = client_acceptable(identity, ssl_obj.getpeercert(binary_form=True) if ssl_obj else None)
        if why:
            log.warning("Model proxy: %s refused: %s", peer[0], why)
            writer.transport.abort()
            return
        try:
            await self._http(reader, writer, str(peer[0]), lambda: self._upstream_port(internal))
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 3)

    def _upstream_port(self, internal: int) -> int:
        return internal

    # ── HTTP ────────────────────────────────────────────────────────

    async def _http(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: str, upstream: Callable[[], int],
    ) -> None:
        lim = self.limits
        conn = h11.Connection(h11.SERVER, max_incomplete_event_size=lim.max_header_bytes)
        first = True
        while True:
            try:
                req = await self._read_head(conn, reader, first=first)
                first = False
            except _Gone:
                return
            except _Refused as exc:
                await self._reply(conn, writer, exc.status, exc.message)
                return
            except h11.RemoteProtocolError as exc:
                await self._reply(conn, writer, 431 if "too big" in str(exc).lower() else 400, "malformed request")
                return
            except TimeoutError:
                return
            if req is None:
                return
            try:
                keep = await self._exchange(conn, reader, writer, req, peer, upstream())
            except _Refused as exc:
                await self._reply(conn, writer, exc.status, exc.message, exc.headers)
                return
            except (_Gone, ConnectionError, TimeoutError, OSError, ssl.SSLError, h11.ProtocolError):
                return
            if not keep or conn.our_state is not h11.DONE or conn.their_state is not h11.DONE:
                return
            conn.start_next_cycle()

    async def _read_head(
        self, conn: h11.Connection, reader: asyncio.StreamReader, *, first: bool,
    ) -> h11.Request | None:
        lim = self.limits
        loop = asyncio.get_running_loop()
        started: float | None = None
        raw = b""
        while True:
            ev = conn.next_event()
            if ev is h11.NEED_DATA:
                wait = (lim.header_timeout if first else lim.idle_timeout) if started is None else max(
                    0.01, started + lim.header_timeout - loop.time(),
                )
                data = await asyncio.wait_for(reader.read(65536), wait)
                if started is None and data:
                    started = loop.time()
                raw += data
                conn.receive_data(data)
                continue
            if isinstance(ev, h11.Request):
                # h11 merges identical duplicate Content-Length lines; a proxy must not.
                if raw.find(b"\r\n\r\n") > lim.max_header_bytes:
                    raise _Refused(431, "request headers too large")
                lines = raw.split(b"\r\n\r\n", 1)[0].lower().split(b"\r\n")[1:]
                if sum(1 for ln in lines if ln.split(b":", 1)[0].strip() == b"content-length") > 1:
                    raise _Refused(400, "duplicate Content-Length")
                return ev
            if isinstance(ev, h11.ConnectionClosed) or ev is h11.PAUSED:
                return None
            raise h11.RemoteProtocolError("unexpected event")

    @staticmethod
    def _check(req: h11.Request, lim: ProxyLimits) -> None:
        headers = req.headers
        names = [k for k, _ in headers]
        if names.count(b"content-length") > 1:
            raise _Refused(400, "duplicate Content-Length")
        if names.count(b"transfer-encoding") > 1:
            raise _Refused(400, "duplicate Transfer-Encoding")
        if b"content-length" in names and b"transfer-encoding" in names:
            raise _Refused(400, "Content-Length with Transfer-Encoding")
        if req.http_version != b"1.1":
            raise _Refused(505, "HTTP/1.1 only")
        if len(headers) > lim.max_headers:
            raise _Refused(431, "too many headers")
        target = req.target.decode("ascii", "replace")
        path = target.split("?", 1)[0]
        allowed = ALLOWLIST.get(path)
        if allowed is None or not target.startswith("/"):
            raise _Refused(403, "this path is not served here")
        method = req.method.decode("ascii", "replace")
        if method not in allowed:
            raise _Refused(405, "method not allowed", [("allow", ", ".join(sorted(allowed)))])
        for k, v in headers:
            if k == b"expect" and v.lower() != b"100-continue":
                raise _Refused(417, "expectation failed")
            if k == b"content-length" and int(v) > lim.max_body_bytes:
                raise _Refused(413, "request body too large")

    async def _reply(
        self, conn: h11.Connection, writer: asyncio.StreamWriter, status: int, message: str,
        extra: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps({"error": {"message": message, "type": "proxy_error", "code": status}}).encode()
        headers = [("content-type", "application/json"), ("content-length", str(len(body))),
                   ("connection", "close"), *(extra or [])]
        try:
            writer.write(conn.send(h11.Response(status_code=status, headers=headers)) or b"")
            writer.write(conn.send(h11.Data(data=body)) or b"")
            writer.write(conn.send(h11.EndOfMessage()) or b"")
            await asyncio.wait_for(writer.drain(), 5)
        except Exception:  # noqa: BLE001 - the connection is closed next anyway
            log.debug("Model proxy: could not send %s", status, exc_info=True)

    async def _exchange(
        self, conn: h11.Connection, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        req: h11.Request, peer: str, internal: int,
    ) -> bool:
        lim = self.limits
        self._check(req, lim)
        chunked = any(k == b"transfer-encoding" for k, _ in req.headers)
        if conn.they_are_waiting_for_100_continue:
            writer.write(conn.send(h11.InformationalResponse(status_code=100, headers=[])) or b"")
            await writer.drain()
        host = next((v for k, v in req.headers if k == b"host"), b"").decode("latin-1")
        up_headers: list[tuple[bytes, bytes]] = [(b"host", f"127.0.0.1:{internal}".encode())]
        connection_tokens = {
            t.strip().lower().encode()
            for k, v in req.headers if k == b"connection" for t in v.decode("latin-1").split(",")
        }
        for k, v in req.headers:
            if k in _HOP_BY_HOP or k in _NEVER_FORWARD or k.startswith(b"x-forwarded-") or k in connection_tokens:
                continue
            up_headers.append((k, v))
        if chunked:
            up_headers.append((b"transfer-encoding", b"chunked"))
        up_headers += [
            (b"x-forwarded-for", peer.encode()), (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", host.encode("latin-1")), (b"connection", b"close"),
        ]
        try:
            ur, uw = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", internal), lim.connect_timeout)
        except (OSError, TimeoutError) as exc:
            raise _Refused(502, "the model server is not reachable") from exc
        uconn = h11.Connection(h11.CLIENT, max_incomplete_event_size=lim.max_header_bytes * 4)
        try:
            uw.write(uconn.send(h11.Request(method=req.method, target=req.target, headers=up_headers)) or b"")
            total = 0
            while True:
                ev = await self._body_event(conn, reader)
                if isinstance(ev, h11.Data):
                    total += len(ev.data)
                    if total > lim.max_body_bytes:
                        raise _Refused(413, "request body too large")
                    uw.write(uconn.send(ev) or b"")
                    await asyncio.wait_for(uw.drain(), lim.write_timeout)
                elif isinstance(ev, h11.EndOfMessage):
                    uw.write(uconn.send(h11.EndOfMessage()) or b"")
                    await asyncio.wait_for(uw.drain(), lim.write_timeout)
                    break
                else:
                    raise _Gone
            return await self._relay(conn, reader, writer, uconn, ur)
        finally:
            with contextlib.suppress(Exception):
                uw.transport.abort()

    async def _body_event(self, conn: h11.Connection, reader: asyncio.StreamReader) -> Any:
        while True:
            try:
                ev = conn.next_event()
            except h11.RemoteProtocolError as exc:
                raise _Refused(400, "malformed body") from exc
            if ev is h11.NEED_DATA:
                data = await asyncio.wait_for(reader.read(65536), self.limits.body_timeout)
                conn.receive_data(data)
                continue
            if ev is h11.PAUSED:
                raise _Gone
            return ev

    async def _relay(
        self, conn: h11.Connection, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        uconn: h11.Connection, ureader: asyncio.StreamReader,
    ) -> bool:
        """Stream the upstream response down; end it if the client goes first."""
        relay = asyncio.ensure_future(self._relay_response(conn, writer, uconn, ureader))
        watch = asyncio.ensure_future(reader.read(65536))
        try:
            done, _ = await asyncio.wait({relay, watch}, return_when=asyncio.FIRST_COMPLETED)
            if relay not in done:
                data = watch.result()
                if not data:  # the client closed: the upstream goes with it, vLLM aborts
                    relay.cancel()
                    with contextlib.suppress(BaseException):
                        await relay
                    return False
                await relay  # a pipelined request is not served: this response, then the connection closes
                return False
            return relay.result()
        finally:
            for task in (relay, watch):
                if not task.done():
                    task.cancel()

    async def _relay_response(
        self, conn: h11.Connection, writer: asyncio.StreamWriter, uconn: h11.Connection, ureader: asyncio.StreamReader,
    ) -> bool:
        lim = self.limits
        started = False
        try:
            while True:
                ev = uconn.next_event()
                if ev is h11.NEED_DATA:
                    uconn.receive_data(await asyncio.wait_for(ureader.read(65536), lim.upstream_read_timeout))
                    continue
                if isinstance(ev, h11.InformationalResponse):
                    continue
                if isinstance(ev, h11.Response):
                    drop = {b"connection", b"keep-alive", b"proxy-authenticate", b"te", b"trailer", b"upgrade",
                            b"transfer-encoding", b"proxy-connection"}
                    headers = [(k, v) for k, v in ev.headers if k not in drop]
                    writer.write(conn.send(h11.Response(
                        status_code=ev.status_code, headers=headers, reason=ev.reason,
                    )) or b"")
                    started = True
                elif isinstance(ev, h11.Data):
                    writer.write(conn.send(h11.Data(data=bytes(ev.data))) or b"")
                    await asyncio.wait_for(writer.drain(), lim.write_timeout)
                elif isinstance(ev, h11.EndOfMessage):
                    writer.write(conn.send(h11.EndOfMessage()) or b"")
                    await asyncio.wait_for(writer.drain(), lim.write_timeout)
                    return True
                else:  # the upstream closed mid-response
                    raise ConnectionError("upstream closed")
        except (OSError, h11.ProtocolError, TimeoutError) as exc:
            if started:
                raise ConnectionError("upstream failed mid-response") from exc  # truncated: the client sees it
            status = 504 if isinstance(exc, TimeoutError) else 502
            raise _Refused(status, "the model server did not answer") from exc


# ── the self-test ───────────────────────────────────────────────────────


def self_test(limits: ProxyLimits | None = None) -> str | None:
    """Run a throwaway proxy end to end; ``None`` when it works, else why not.

    Ephemeral CA and certificates, an in-process upstream, a real TLS client:
    the good client reaches ``/health``, a stranger and a missing certificate
    do not, a path off the allowlist is refused.
    """
    import tempfile  # noqa: PLC0415

    from faden_agent import pki_util  # noqa: PLC0415

    proxy = NativeProxy(limits or ProxyLimits(), bind_hosts=("127.0.0.1",))
    upstream_server: list[Any] = []
    try:
        ca_key, ca = pki_util.make_ca()
        s_key, s_cert = pki_util.issue(ca_key, ca, "selftest", ips=("127.0.0.1",), eku=(pki_util.SERVER_AUTH,))
        g_key, g_cert = pki_util.issue(ca_key, ca, "fadenstack-gateway")
        o_key, o_cert = pki_util.issue(ca_key, ca, "someone-else")
        with tempfile.TemporaryDirectory(prefix="faden-proxy-selftest-") as tmp:
            d = Path(tmp)
            (d / "s.crt").write_bytes(pki_util.cert_pem(s_cert))
            (d / "s.key").write_bytes(pki_util.key_pem(s_key))
            (d / "ca.pem").write_bytes(pki_util.cert_pem(ca))
            (d / "g.crt").write_bytes(pki_util.cert_pem(g_cert))
            (d / "g.key").write_bytes(pki_util.key_pem(g_key))
            (d / "o.crt").write_bytes(pki_util.cert_pem(o_cert))
            (d / "o.key").write_bytes(pki_util.key_pem(o_key))
            proxy.set_identity(build_identity(d / "s.crt", d / "s.key", d / "ca.pem", "fadenstack-gateway"))
            proxy.start()

            async def _upstream() -> int:
                async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
                    await r.readuntil(b"\r\n\r\n")
                    w.write(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")
                    await w.drain()
                    w.close()

                srv = await asyncio.start_server(handle, "127.0.0.1", 0)
                upstream_server.append(srv)
                return srv.sockets[0].getsockname()[1]

            up_port = asyncio.run_coroutine_threadsafe(_upstream(), proxy._loop).result(5)  # type: ignore[arg-type]  # noqa: SLF001
            listener = asyncio.run_coroutine_threadsafe(proxy._bind(0), proxy._loop).result(5)  # type: ignore[arg-type]  # noqa: SLF001
            listener.internal = up_port
            proxy._listeners[listener.port] = listener  # noqa: SLF001
            port = listener.port

            def request(client: tuple[str, str] | None, path: str) -> str:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.load_verify_locations(cafile=str(d / "ca.pem"))
                if client:
                    ctx.load_cert_chain(str(d / client[0]), str(d / client[1]))
                with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                    tls.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
                    data = b""
                    while chunk := tls.recv(4096):
                        data += chunk
                return data.split(b"\r\n", 1)[0].decode("latin-1")

            if "200" not in request(("g.crt", "g.key"), "/health"):
                return "the gateway's certificate did not reach /health"
            if "403" not in request(("g.crt", "g.key"), "/metrics"):
                return "a path off the allowlist was not refused"
            for stranger in ((("o.crt", "o.key")), None):
                try:
                    reply = request(stranger, "/health")
                except (ssl.SSLError, OSError):
                    continue
                if "200" in reply:
                    return "a client that is not the gateway was let through"
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means the proxy cannot be trusted
        return f"{type(exc).__name__}: {exc}"
    finally:
        proxy.stop()
