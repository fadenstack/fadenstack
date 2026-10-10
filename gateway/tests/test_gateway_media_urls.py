"""Media URLs a client sends are fetched by the gateway; the model gets data: URLs.

vLLM fetches an image_url (audio_url, video_url, ...) itself, with no list of
allowed domains, following redirects, resolving the host again; LiteLLM
downloads some for the providers that take no URLs. Checking the URL in the
gateway and letting them fetch it still had a public URL that redirects to
the metadata service, or a name whose DNS answer changes, fetched from inside.
Now the gateway refuses, with a 400, a media URL whose host is not public,
fetches the others itself -- connecting only to the address it checked,
following redirects itself, within a size cap and a timeout, of the part's
kind -- and the model gets a data: URL. The pictures the gateway adds itself
(a chat's attachments) and the pictures the SDKs send (data: URLs) go on as
they are.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import ssl
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatAttachment, ChatMessage, ExtractionStatus, ProviderType
from faden_gateway.services.gateway import media_fetch, media_urls, upstream_tls
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.settings import settings
from tests.test_agent_routes import URL as AGENT_URL
from tests.test_agent_routes import _chat as _agent_chat
from tests.test_agent_routes import _headers as _agent_headers
from tests.test_agent_routes import _model as _agent_model
from tests.test_agent_routes import backend  # noqa: F401 - the fixture the agent route needs
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _model_answers,
    _Observability,
    _seed,
    _session,
    _token,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
CAT = "https://pictures.example.com/cat.png"
PUBLIC = {
    "pictures.example.com": ["93.184.216.34"],
    "cdn.example.net": ["93.184.216.35"],
    "two-faced.example.com": ["93.184.216.34", "10.0.0.8"],
}


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names resolve from PUBLIC (IP literals as themselves); any other name does not resolve."""

    async def resolve(host: str) -> list[str]:
        if host in PUBLIC:
            return PUBLIC[host]
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return []

    monkeypatch.setattr(media_urls, "resolve", resolve)
    monkeypatch.setattr(settings, "media_url_check", True)
    monkeypatch.setattr(settings, "media_url_allowed_hosts", "")
    monkeypatch.setattr(settings, "media_url_max_bytes", 20 * 1024 * 1024)
    monkeypatch.setattr(settings, "media_url_timeout_sec", 20.0)
    media_urls._allowed.cache_clear()  # noqa: SLF001


class _Peer:
    """The network stream httpcore puts on a response: here only the address the socket reached."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.closed = False

    def get_extra_info(self, info: str) -> Any:
        return (self.address, 443) if info == "server_addr" else None

    async def aclose(self) -> None:
        self.closed = True


class _Web:
    """The internet as the gateway's media fetch sees it: pages by URL, and every request it was sent."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: Any = PNG, *, status: int = 200, peer: str | None = None, **headers: str) -> None:
        self.pages[url] = {"body": body, "status": status, "peer": peer, "headers": {
            k.replace("_", "-"): v for k, v in ({"content_type": "image/png"} | headers).items()
        }}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = self.pages.get(str(request.url), {"body": b"", "status": 404, "peer": None, "headers": {}})
        host = request.url.host
        peer = page["peer"] or PUBLIC.get(host, [host])[0]
        return httpx.Response(
            page["status"], headers=page["headers"], content=page["body"],
            extensions={"network_stream": _Peer(peer)},
        )

    def asked(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> _Web:
    """The gateway's media client, answered by :class:`_Web`; it serves CAT."""
    found = _Web()
    found.add(CAT)
    mock = media_fetch.make_client(httpx.MockTransport(found.handler))
    monkeypatch.setattr(media_fetch, "client", lambda: mock)
    return found


def _picture(url: Any, kind: str = "image_url") -> list[dict[str, Any]]:
    return [{"role": "user", "content": [{"type": "text", "text": "What is in this picture?"}, {"type": kind, kind: url}]}]


def _parts(sent: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for m in sent["messages"] if isinstance(m["content"], list) for p in m["content"]]


async def _refused(url: str, kind: str = "image") -> GatewayError:
    with pytest.raises(GatewayError) as refused:
        await media_fetch.fetch("messages[0].content[1].image_url.url", url, kind)
    assert refused.value.status_code == 400
    assert refused.value.param == "messages[0].content[1].image_url.url"
    return refused.value


# ── what counts as a media URL ──────────────────────────────────────────


def test_media_urls_are_found_in_every_shape_a_server_takes() -> None:
    messages = [
        {"role": "system", "content": "See https://intranet.lan/policy for the rules."},
        {"role": "user", "content": [
            {"type": "text", "text": "compare http://10.0.0.1/a with this"},
            {"type": "image_url", "image_url": {"url": "http://10.0.0.1/a.png", "detail": "high"}},
            {"type": "input_image", "image_url": "http://10.0.0.2/b.png"},
            {"image_url": {"url": "http://10.0.0.3/c.png"}},  # vLLM takes a part by its key
            {"type": "audio_url", "audio_url": {"url": "http://10.0.0.4/d.wav"}},
            {"type": "video_url", "video_url": {"url": "http://10.0.0.5/e.mp4"}},
            {"type": "input_audio", "input_audio": {"data": "UklGRiQAAABXQVZF", "format": "wav"}},
            {"type": "image", "source": {"type": "url", "url": "http://10.0.0.6/f.png"}},
            {"type": "file", "file": {"file_data": DATA_URL, "filename": "scan.png"}},
            {"type": "image_url", "image_url": {"url": "http:/10.0.0.7/g.png"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "the page"}, {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]},
    ]
    found = list(media_urls.media_sources(messages))
    assert [url for _, url in found] == [
        "http://10.0.0.1/a.png", "http://10.0.0.2/b.png", "http://10.0.0.3/c.png", "http://10.0.0.4/d.wav",
        "http://10.0.0.5/e.mp4", "http://10.0.0.6/f.png", DATA_URL, "http:/10.0.0.7/g.png", DATA_URL,
    ], "text, captions and base64 data are not media URLs"
    assert found[0][0] == "messages[1].content[1].image_url.url"
    assert found[-1][0] == "messages[2].content[1].image_url.url"
    kinds = dict(media_urls.wanted(messages).keys())  # url -> kind
    assert kinds["http://10.0.0.3/c.png"] == "image"
    assert kinds["http://10.0.0.4/d.wav"] == "audio"
    assert kinds["http://10.0.0.5/e.mp4"] == "video"
    assert DATA_URL not in kinds, "a data: URL is never fetched"


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "8.8.8.8"],
)
def test_public_addresses_are_public(address: str) -> None:
    assert media_urls.is_public(address)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "::1", "10.1.2.3", "172.17.0.1", "192.168.0.10", "fd12::1",  # loopback, private
        "169.254.169.254", "fe80::1", "fd00:ec2::254", "100.100.100.200",  # link-local, metadata
        "100.64.0.1",  # CGNAT
        "224.0.0.1", "ff02::1",  # multicast
        "240.0.0.1", "255.255.255.255", "0.0.0.0", "::",  # noqa: S104 - reserved, unspecified
        "::ffff:10.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1",  # IPv4 inside IPv6
        "not-an-address",
    ],
)
def test_internal_addresses_are_not(address: str) -> None:
    assert not media_urls.is_public(address)


@pytest.mark.anyio
async def test_a_url_passes_only_when_every_address_is_public() -> None:
    check = media_urls.check_url
    await check("x", DATA_URL, allowed_hosts="")
    await check("x", CAT, allowed_hosts="")
    for url in (
        "https://two-faced.example.com/cat.png",  # one of its addresses is private
        "http://does-not-resolve.example/cat.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/x.png",
        "http://localhost:8000/x.png",
        "ftp://pictures.example.com/cat.png",
        "file:///etc/passwd",
        "//pictures.example.com/cat.png",
        "http:/10.0.0.1/cat.png",  # no authority here; a lenient parser finds one
        "http://10.0.0.1\\@pictures.example.com/cat.png",  # the host checked would not be the host fetched
        "http://pictures.example.com\t/cat.png",
    ):
        with pytest.raises(GatewayError) as refused:
            await check("messages[0].content[1].image_url.url", url, allowed_hosts="")
        assert refused.value.status_code == 400
        assert refused.value.code == "media_url_not_allowed"
        assert refused.value.param == "messages[0].content[1].image_url.url"


@pytest.mark.anyio
async def test_allowed_hosts_let_internal_ones_through() -> None:
    allowed = "media.lan, *.pictures.lan, 10.20.0.0/16, 192.168.5.7"
    for url in (
        "http://media.lan/a.png", "http://cdn.pictures.lan/a.png", "http://10.20.3.4/a.png", "http://192.168.5.7/a.png",
    ):
        await media_urls.check_url("x", url, allowed_hosts=allowed)
    for url in ("http://other.lan/a.png", "http://pictures.lan.evil.example/a.png", "http://10.21.0.1/a.png"):
        with pytest.raises(GatewayError):
            await media_urls.check_url("x", url, allowed_hosts=allowed)


# ── the gateway fetches them: where it connects ─────────────────────────


@pytest.mark.parametrize(
    ("host", "peer", "allowed", "expected"),
    [
        ("pictures.example.com", ("93.184.216.34", 443), "", True),
        ("pictures.example.com", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0), "", True),
        ("pictures.example.com", ("10.0.0.5", 443), "", False),  # the name was public; the socket is not
        ("pictures.example.com", ("169.254.169.254", 80), "", False),
        ("pictures.example.com", ("127.0.0.1", 443), "", False),
        ("pictures.example.com", None, "", False),  # nothing says where it went: not taken
        ("pictures.example.com", ("10.20.0.9", 443), "10.20.0.0/16", True),
        ("media.lan", ("10.30.0.9", 80), "media.lan", True),  # an allowed host's internal addresses
        ("other.lan", ("10.30.0.9", 80), "media.lan", False),
    ],
)
def test_the_address_a_connection_reached_is_checked(
    host: str, peer: Any, allowed: str, expected: bool,
) -> None:
    assert media_fetch.peer_allowed(host, peer, allowed) is expected


class _Network:
    """The network below the pinned backend: what it was asked to connect to, and where that went."""

    def __init__(self, reached: str | None = None) -> None:
        self.asked: list[str] = []
        self.reached = reached
        self.streams: list[_Peer] = []

    async def connect_tcp(self, host: str, port: int, **_: Any) -> _Peer:
        self.asked.append(host)
        stream = _Peer(self.reached or host)
        self.streams.append(stream)
        return stream

    async def sleep(self, seconds: float) -> None: ...


@pytest.mark.anyio
async def test_the_pinned_backend_connects_to_the_address_it_checked() -> None:
    below = _Network()
    pinned = media_fetch.PinnedBackend(below)  # type: ignore[arg-type]

    stream = await pinned.connect_tcp("pictures.example.com", 443)
    assert below.asked == ["93.184.216.34"], "to the address, not the name: nobody resolves it again"
    assert stream.get_extra_info("server_addr") == ("93.184.216.34", 443)

    for host in ("two-faced.example.com", "does-not-resolve.example", "169.254.169.254"):
        with pytest.raises(media_fetch.MediaAddressRefusedError):
            await pinned.connect_tcp(host, 443)
    assert below.asked == ["93.184.216.34"], "a refused host is not connected to at all"


@pytest.mark.anyio
async def test_the_pinned_backend_refuses_a_socket_that_reached_another_address() -> None:
    below = _Network(reached="10.0.0.5")
    with pytest.raises(media_fetch.MediaAddressRefusedError):
        await media_fetch.PinnedBackend(below).connect_tcp("pictures.example.com", 443)  # type: ignore[arg-type]
    assert below.streams[0].closed


@pytest.mark.anyio
async def test_the_pinned_backend_tries_the_next_address_when_one_fails() -> None:
    class _FirstFails(_Network):
        async def connect_tcp(self, host: str, port: int, **kw: Any) -> _Peer:
            if not self.asked:
                self.asked.append(host)
                raise httpcore.ConnectError("unreachable")
            return await super().connect_tcp(host, port, **kw)

    PUBLIC["dual.example.com"] = ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"]
    try:
        below = _FirstFails()
        await media_fetch.PinnedBackend(below).connect_tcp("dual.example.com", 443)  # type: ignore[arg-type]
    finally:
        del PUBLIC["dual.example.com"]
    assert below.asked == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"], "IPv4 first, then the next"


@pytest.mark.anyio
async def test_the_real_transport_fetches_only_from_the_address_it_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transport as the gateway runs it (httpx, httpcore), against a server on this machine."""
    seen: list[str] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head.split(b"\r\n", 1)[0].decode())
        if b" /cat.png " in head:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                % len(PNG) + PNG,
            )
        else:
            writer.write(
                b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/meta-data/\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n",
            )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    real = media_fetch.make_client()
    monkeypatch.setattr(media_fetch, "client", lambda: real)
    try:
        monkeypatch.setattr(settings, "media_url_allowed_hosts", "127.0.0.1")
        assert await media_fetch.fetch("x", f"http://127.0.0.1:{port}/cat.png", "image") == DATA_URL

        refused = await _refused(f"http://127.0.0.1:{port}/elsewhere")
        assert refused.code == "media_url_not_allowed"
        assert "redirects to 169.254.169.254" in refused.message
        assert seen == ["GET /cat.png HTTP/1.1", "GET /elsewhere HTTP/1.1"], "the metadata service was never asked"

        # A name that answers public when checked and loopback when connecting (DNS rebinding).
        monkeypatch.setattr(settings, "media_url_allowed_hosts", "")
        answers = iter([["93.184.216.34"], ["127.0.0.1"]])

        async def rebinding(host: str) -> list[str]:
            return next(answers)

        monkeypatch.setattr(media_urls, "resolve", rebinding)
        refused = await _refused(f"http://rebind.example.com:{port}/cat.png")
        assert refused.code == "media_url_not_allowed"
        assert len(seen) == 2, "nothing reached the server"
    finally:
        await real.aclose()
        server.close()
        await server.wait_closed()


def _tls_pair(name: str) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """A server context with a certificate for *name*, from a CA a client context trusts."""
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "media test CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    chain = leaf.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    upstream_tls._load_chain(server, chain, key_pem)  # noqa: SLF001 - its files-on-disk dance, reused
    client = ssl.create_default_context(cadata=ca.public_bytes(serialization.Encoding.PEM).decode())
    return server, client


@pytest.mark.anyio
async def test_over_tls_the_certificate_is_checked_against_the_name_while_the_socket_goes_to_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned to the address it checked, the connection still verifies the host name in the URL."""
    server_ctx, client_ctx = _tls_pair("pictures.test")
    monkeypatch.setattr(upstream_tls, "ssl_context", lambda tls: client_ctx)

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            writer.close()
            return
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
            % len(PNG) + PNG,
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setitem(PUBLIC, "pictures.test", ["127.0.0.1"])
    monkeypatch.setitem(PUBLIC, "impostor.test", ["127.0.0.1"])
    monkeypatch.setattr(settings, "media_url_allowed_hosts", "127.0.0.0/8")
    real = media_fetch.make_client()
    monkeypatch.setattr(media_fetch, "client", lambda: real)
    try:
        assert await media_fetch.fetch("x", f"https://pictures.test:{port}/cat.png", "image") == DATA_URL
        refused = await _refused(f"https://impostor.test:{port}/cat.png")
        assert refused.code == "media_url_unreachable", "the certificate is for pictures.test, not impostor.test"
    finally:
        await real.aclose()
        server.close()
        await server.wait_closed()


# ── the gateway fetches them: what it takes ─────────────────────────────


@pytest.mark.anyio
async def test_a_redirect_to_an_internal_address_is_refused_and_never_asked(web: _Web) -> None:
    web.add("https://cdn.example.net/cat.png", status=302, location="http://169.254.169.254/latest/meta-data/iam/")
    refused = await _refused("https://cdn.example.net/cat.png")
    assert refused.code == "media_url_not_allowed"
    assert "redirects to 169.254.169.254" in refused.message
    assert web.asked() == ["https://cdn.example.net/cat.png"]


@pytest.mark.anyio
async def test_public_redirects_are_followed_a_few_times(web: _Web) -> None:
    web.add("https://cdn.example.net/a", status=301, location="/b")
    web.add("https://cdn.example.net/b", status=307, location=CAT)
    assert await media_fetch.fetch("x", "https://cdn.example.net/a", "image") == DATA_URL
    assert web.asked() == ["https://cdn.example.net/a", "https://cdn.example.net/b", CAT]

    for i in range(media_fetch.MAX_REDIRECTS + 1):
        web.add(f"https://cdn.example.net/loop{i}", status=302, location=f"/loop{i + 1}")
    web.add(f"https://cdn.example.net/loop{media_fetch.MAX_REDIRECTS + 1}")
    refused = await _refused("https://cdn.example.net/loop0")
    assert refused.code == "media_url_unreachable"
    assert "redirects more than" in refused.message


@pytest.mark.anyio
async def test_an_answer_from_another_address_than_the_one_checked_is_refused(web: _Web) -> None:
    web.add(CAT, peer="10.0.0.5")
    refused = await _refused(CAT)
    assert refused.code == "media_url_not_allowed"
    assert "answered from an address that is not public" in refused.message


@pytest.mark.anyio
async def test_the_size_cap_holds_for_the_whole_request_as_it_streams(
    web: _Web, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_url_max_bytes", 1000)
    web.add("https://cdn.example.net/said.png", b"x" * 2000)  # its Content-Length says so: not read
    assert (await _refused("https://cdn.example.net/said.png")).code == "media_url_too_large"

    pulled: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(100):
            pulled.append(i)
            yield b"x" * 300

    web.add("https://cdn.example.net/streamed.png", chunks())  # no length: counted as it comes
    refused = await _refused("https://cdn.example.net/streamed.png")
    assert refused.code == "media_url_too_large"
    assert "FADEN_GATEWAY_MEDIA_URL_MAX_BYTES" in refused.message
    assert len(pulled) <= 5, "it stopped reading at the cap"

    web.add("https://cdn.example.net/a.png", b"x" * 600)
    web.add("https://cdn.example.net/b.png", b"x" * 600)
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://cdn.example.net/a.png"}},
        {"type": "image_url", "image_url": {"url": "https://cdn.example.net/b.png"}},
    ]}]
    with pytest.raises(GatewayError) as both:
        await media_fetch.fetch_messages(messages)
    assert both.value.code == "media_url_too_large", "600 + 600 is more than the request's 1000"


@pytest.mark.anyio
async def test_what_comes_back_must_be_the_parts_kind(web: _Web) -> None:
    web.add("https://cdn.example.net/page", b"<html>sign in</html>", content_type="text/html; charset=utf-8")
    refused = await _refused("https://cdn.example.net/page")
    assert refused.code == "media_url_unsupported_type"
    assert "text/html, not a picture" in refused.message
    assert (await _refused(CAT, kind="audio")).code == "media_url_unsupported_type"

    # A server that says nothing about the type: the name's extension does.
    web.add("https://cdn.example.net/cat2.png", content_type="application/octet-stream")
    assert await media_fetch.fetch("x", "https://cdn.example.net/cat2.png", "image") == DATA_URL
    web.add("https://cdn.example.net/blob", content_type="binary/octet-stream")
    assert (await _refused("https://cdn.example.net/blob")).code == "media_url_unsupported_type"

    # A file part takes any file, not a web page.
    web.add("https://cdn.example.net/report.pdf", b"%PDF-1.7", content_type="application/pdf")
    pdf = await media_fetch.fetch("x", "https://cdn.example.net/report.pdf", "file")
    assert pdf == "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.7").decode()
    assert (await _refused("https://cdn.example.net/page", kind="file")).code == "media_url_unsupported_type"


@pytest.mark.anyio
async def test_a_url_that_cannot_be_fetched_is_a_clear_400(web: _Web, monkeypatch: pytest.MonkeyPatch) -> None:
    refused = await _refused("https://cdn.example.net/missing.png")
    assert refused.code == "media_url_unreachable"
    assert "answered 404" in refused.message

    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(media_fetch, "client", lambda: media_fetch.make_client(httpx.MockTransport(down)))
    assert (await _refused(CAT)).code == "media_url_unreachable"

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    monkeypatch.setattr(settings, "media_url_timeout_sec", 0.05)
    monkeypatch.setattr(media_fetch, "client", lambda: media_fetch.make_client(httpx.MockTransport(slow)))
    refused = await _refused(CAT)
    assert refused.code == "media_url_unreachable"
    assert "within 0.05 s" in refused.message


@pytest.mark.anyio
async def test_nothing_is_looked_up_or_fetched_for_messages_without_media_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_client() -> httpx.AsyncClient:
        raise AssertionError("no media client for a request without media URLs")

    async def no_lookup(host: str) -> list[str]:
        raise AssertionError("no lookup for a request without media URLs")

    monkeypatch.setattr(media_fetch, "client", no_client)
    monkeypatch.setattr(media_urls, "resolve", no_lookup)
    messages = [
        {"role": "user", "content": "Describe https://pictures.example.com/cat.png"},
        {"role": "user", "content": [{"type": "text", "text": "and this"}, {"type": "image_url", "image_url": {"url": DATA_URL}}]},
    ]
    assert not media_fetch.needed(messages)
    assert await media_fetch.fetch_messages(messages) == {}
    payload = {"model": ALIAS, "messages": messages}
    assert await media_fetch.inline(payload, {}) is payload

    monkeypatch.setattr(settings, "media_url_check", False)
    switched_off = _picture({"url": "http://169.254.169.254/latest/"})
    assert not media_fetch.needed(switched_off)
    assert await media_fetch.fetch_messages(switched_off) == {}
    payload = {"model": ALIAS, "messages": switched_off}
    assert await media_fetch.inline(payload, {}) is payload, "switched off, the URL goes on as it is"


@pytest.mark.anyio
async def test_what_leaves_has_only_data_urls_and_the_clients_messages_are_not_changed(web: _Web) -> None:
    web.add("https://cdn.example.net/report.pdf", b"%PDF-1.7", content_type="application/pdf")
    client_parts = [
        {"type": "text", "text": "Compare"},
        {"type": "image_url", "image_url": {"url": CAT, "detail": "low"}},
        {"type": "file", "file": {"file_id": "https://cdn.example.net/report.pdf", "filename": "report.pdf"}},
    ]
    messages = [{"role": "user", "content": client_parts}]
    fetched = await media_fetch.fetch_messages(messages)
    assert set(fetched) == {(CAT, "image"), ("https://cdn.example.net/report.pdf", "file")}

    payload = {"model": ALIAS, "messages": [{"role": "system", "content": "today"}, *messages]}
    out = await media_fetch.inline(payload, fetched)
    parts = out["messages"][1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": DATA_URL, "detail": "low"}}
    assert parts[2] == {"type": "file", "file": {
        "file_data": "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.7").decode(), "filename": "report.pdf",
    }}, "a fetched file goes in file_data"
    assert client_parts[1]["image_url"]["url"] == CAT, "the client's message objects are left as they were"
    assert len(web.requests) == 2

    # A URL only the outgoing payload has (none should) is fetched there, not sent on.
    late = {"model": ALIAS, "messages": [{"role": "tool", "tool_call_id": "c", "content": [
        {"type": "image_url", "image_url": {"url": CAT}},
    ]}]}
    assert (await media_fetch.inline(late, {}))["messages"][0]["content"][0]["image_url"]["url"] == DATA_URL
    internal = {"model": ALIAS, "messages": _picture({"url": "http://10.0.0.7/x.png"})}
    with pytest.raises(GatewayError):
        await media_fetch.inline(internal, {})


# ── the routes ───────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ({"url": "http://169.254.169.254/latest/meta-data/iam/"}, "image_url"),
        ({"url": "http://10.0.0.7:8080/admin"}, "audio_url"),
        ({"url": "http://localhost:6379/"}, "video_url"),
    ],
)
async def test_an_internal_media_url_is_refused_before_the_model_is_asked(
    stream: bool, url: dict[str, str], kind: str, web: _Web,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": _picture(url, kind),
    })

    assert r.status_code == 400, r.text
    error = r.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["code"] == "media_url_not_allowed"
    assert error["param"] == f"messages[0].content[1].{kind}.url"
    assert "data: URL" in error["message"]
    assert sent == [], "the model was not asked"
    assert web.requests == [], "nor was the address"


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_redirect_to_the_metadata_service_is_refused_on_the_route(
    stream: bool, web: _Web,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)
    web.add("https://cdn.example.net/cat.png", status=302, location="http://169.254.169.254/latest/meta-data/")

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": _picture({"url": "https://cdn.example.net/cat.png"}),
    })

    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "media_url_not_allowed"
    assert sent == []
    assert web.asked() == ["https://cdn.example.net/cat.png"]


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_data_urls_go_on_untouched_and_public_urls_as_what_the_gateway_fetched(
    stream: bool, web: _Web,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)
    sdk_picture = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0jpeg").decode()

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Compare them"},
            {"type": "image_url", "image_url": {"url": sdk_picture}},
            {"type": "image_url", "image_url": {"url": CAT}},
        ]}],
    })

    assert r.status_code == 200, r.text
    urls = [p["image_url"]["url"] for p in _parts(sent[0]) if p.get("type") == "image_url"]
    assert urls == [sdk_picture, DATA_URL], "the SDK's data: URL as it was; the public URL as its bytes"
    assert "https://" not in str(sent[0]["messages"]), "no URL reaches the model server"
    assert web.asked() == [CAT], "fetched once, by the gateway"


@pytest.mark.anyio
async def test_the_check_can_be_opened_for_listed_hosts_or_switched_off(
    web: _Web, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)
    for host in ("10.20.0.9", "10.30.0.9"):
        web.add(f"http://{host}/scan.png")

    async def ask(url: str) -> int:
        r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
            "model": ALIAS, "messages": _picture({"url": url}),
        })
        return r.status_code

    assert await ask("http://10.20.0.9/scan.png") == 400
    assert web.requests == []

    monkeypatch.setattr(settings, "media_url_allowed_hosts", "10.20.0.0/16")
    assert await ask("http://10.20.0.9/scan.png") == 200
    assert web.asked() == ["http://10.20.0.9/scan.png"], "an allowed host's media is fetched by the gateway too"
    assert {"type": "image_url", "image_url": {"url": DATA_URL}} in _parts(sent[-1])
    assert await ask("http://10.30.0.9/scan.png") == 400

    monkeypatch.setattr(settings, "media_url_check", False)
    assert await ask("http://10.30.0.9/scan.png") == 200, "as it was before"
    assert {"type": "image_url", "image_url": {"url": "http://10.30.0.9/scan.png"}} in _parts(sent[-1])
    assert web.asked() == ["http://10.20.0.9/scan.png"], "switched off, the URL goes on as it is, unfetched"


@pytest.mark.anyio
async def test_a_chats_own_pictures_still_reach_the_model(
    web: _Web, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console sends text; the gateway adds the chat's pictures as data: URLs, after the check."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()

    class _Store:
        async def get_bytes(self, key: str) -> bytes:
            assert key == "k/cat.png"
            return PNG

    fastapi_app.state.chat_file_store = _Store()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)
    sid = await _session(db_session)
    db_session.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename="cat.png", content_type="image/png",
        size_bytes=len(PNG), storage_key="k/cat.png", extraction_status=ExtractionStatus.SKIPPED,
    ))
    await db_session.commit()

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "What is in the picture?"}],
    })

    assert r.status_code == 200, r.text
    urls = [p["image_url"]["url"] for p in _parts(sent[0]) if p.get("type") == "image_url"]
    assert urls == [DATA_URL]
    assert web.requests == [], "the gateway's own picture is never fetched"


@pytest.mark.anyio
async def test_the_chat_keeps_the_url_the_client_sent_and_the_model_gets_the_picture(
    web: _Web, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored question has the URL, not megabytes of base64; trying it again fetches it again."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)
    sid = await _session(db_session)
    await db_session.commit()

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "session_id": str(sid), "messages": _picture({"url": CAT}),
    })

    assert r.status_code == 200, r.text
    assert {"type": "image_url", "image_url": {"url": DATA_URL}} in _parts(sent[0])
    stored = (await db_session.execute(
        select(ChatMessage).where(ChatMessage.session_id == sid, ChatMessage.role == "user"),
    )).scalars().one()
    assert stored.content_parts_json == _picture({"url": CAT})[0]["content"]


@pytest.mark.anyio
async def test_the_agent_route_checks_them_too_and_takes_the_sdks_pictures(
    web: _Web, client: AsyncClient, db_session: AsyncSession, backend: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The .NET SDK sends a tool's pictures (the PowerPoint agent's slide) as data: URLs in the tool message."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _agent_model(monkeypatch, sent)

    refused = await client.post(AGENT_URL, headers=_agent_headers(), json=_agent_chat(
        messages=_picture({"url": "http://169.254.169.254/latest/"}),
    ))
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "media_url_not_allowed"
    assert sent == []

    call = {"id": "c1", "type": "function", "function": {"name": "read_page", "arguments": "{}"}}
    taken = await client.post(AGENT_URL, headers=_agent_headers(), json=_agent_chat(messages=[
        {"role": "user", "content": [{"type": "text", "text": "Check this slide"}, {"type": "image_url",
                                                                                   "image_url": {"url": DATA_URL}}]},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "Slide 3 rendered"}, {"type": "image_url", "image_url": {"url": CAT}},
        ]},
    ]))
    assert taken.status_code == 200, taken.text
    assert DATA_URL in str(sent[0]["messages"])
    assert CAT not in str(sent[0]["messages"]), "a tool result's picture URL is fetched by the gateway too"
    assert web.asked() == [CAT]
