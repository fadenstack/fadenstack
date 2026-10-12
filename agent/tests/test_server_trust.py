"""The agent learns the backend's authority and follows the backend to HTTPS.

Against real sockets: an HTTPS server with a certificate from an authority no
system trusts, and a plain-HTTP one that redirects to it, the way nginx does
once ``faden tls generate`` has run.
"""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from faden_agent import server_trust
from faden_agent.config import AgentConfig
from faden_agent.server_trust import ServerTrust, https_target


def _authority(tmp: Path) -> tuple[str, Path, Path]:
    """A CA and a certificate for 127.0.0.1 it signed: (ca_pem, cert_file, key_file)."""
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "backend")]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_file, key_file = tmp / "edge.crt", tmp / "edge.key"
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return ca.public_bytes(serialization.Encoding.PEM).decode(), cert_file, key_file


@asynccontextmanager
async def _server(respond: Callable[[str], bytes], tls: ssl.SSLContext | None = None) -> AsyncIterator[int]:
    """A one-route HTTP server on 127.0.0.1; yields its port."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode()
            writer.write(respond(path))
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=tls)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


def _ok(_path: str) -> bytes:
    return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"


def _redirect_to(port: int) -> Callable[[str], bytes]:
    def respond(path: str) -> bytes:
        return (
            f"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://127.0.0.1:{port}{path}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode()

    return respond


def _config(tmp: Path, backend_url: str, monkeypatch: pytest.MonkeyPatch) -> AgentConfig:
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", backend_url)
    monkeypatch.setenv("FADEN_NODE_AGENT_STATE_PATH", str(tmp / "state" / "state.json"))
    monkeypatch.setenv("FADEN_NODE_AGENT_REQUEST_TIMEOUT_SEC", "5")
    # Other tests leave agent settings in the process environment.
    monkeypatch.setenv("FADEN_NODE_AGENT_VERIFY_TLS", "true")
    for name in ("TLS_CA_BUNDLE", "TLS_CLIENT_CERT", "TLS_CLIENT_KEY"):
        monkeypatch.delenv(f"FADEN_NODE_AGENT_{name}", raising=False)
    return AgentConfig.from_env()


def _tls_server(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


@pytest.mark.asyncio
async def test_an_authority_learnt_while_running_counts_for_the_next_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_pem, cert, key = _authority(tmp_path)
    config = _config(tmp_path, "http://127.0.0.1:1", monkeypatch)
    trust = ServerTrust(config)
    async with _server(_ok, _tls_server(cert, key)) as port:
        async with httpx.AsyncClient(verify=trust.verify()) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(f"https://127.0.0.1:{port}/api/health")
            assert trust.add_ca(ca_pem)
            assert not trust.add_ca(ca_pem), "the same authority twice is nothing new"
            # The same client, the same context object: only the next handshake changed.
            assert (await client.get(f"https://127.0.0.1:{port}/api/health")).status_code == 200

    # Kept beside the state, and trusted by the next process too.
    assert (tmp_path / "state" / server_trust.CA_FILE).read_text() == ca_pem
    again = _config(tmp_path, "http://127.0.0.1:1", monkeypatch)
    async with _server(_ok, _tls_server(cert, key)) as port:
        async with httpx.AsyncClient(verify=ServerTrust(again).verify()) as client:
            assert (await client.get(f"https://127.0.0.1:{port}/")).status_code == 200


@pytest.mark.asyncio
async def test_the_agent_follows_its_backend_to_https_and_remembers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_pem, cert, key = _authority(tmp_path)
    async with _server(_ok, _tls_server(cert, key)) as https_port:
        async with _server(_redirect_to(https_port)) as http_port:
            configured = f"http://127.0.0.1:{http_port}"
            config = _config(tmp_path, configured, monkeypatch)
            trust = ServerTrust(config)

            # Not yet trusted: it stays where it is.
            assert await trust.follow_to_https() is None
            assert config.backend_url == configured

            trust.add_ca(ca_pem)
            target = await trust.follow_to_https()
            assert target == f"https://127.0.0.1:{https_port}"
            assert config.backend_url == target

    # A restart with the same configuration starts on HTTPS.
    restarted = _config(tmp_path, configured, monkeypatch)
    ServerTrust(restarted)
    assert restarted.backend_url == target
    # Pointed elsewhere by hand: the move belongs to the old address and is not applied.
    elsewhere = _config(tmp_path, "http://10.0.0.9", monkeypatch)
    ServerTrust(elsewhere)
    assert elsewhere.backend_url == "http://10.0.0.9"
    # configure --set BACKEND_URL forgets it.
    assert server_trust.forget_address(tmp_path / "state")
    fresh = _config(tmp_path, configured, monkeypatch)
    ServerTrust(fresh)
    assert fresh.backend_url == configured


@pytest.mark.asyncio
async def test_an_agent_on_https_never_moves_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, "https://127.0.0.1:1", monkeypatch)
    assert await ServerTrust(config).follow_to_https() is None
    assert config.backend_url == "https://127.0.0.1:1"


def test_only_a_move_to_https_on_the_same_host_is_followed() -> None:
    assert https_target("http://192.0.2.61", "https://192.0.2.61/api/health") == "https://192.0.2.61"
    assert https_target("http://192.0.2.61:8080", "https://192.0.2.61:8443/x") == "https://192.0.2.61:8443"
    assert https_target("http://Box.lan", "https://box.lan/") == "https://box.lan"
    assert https_target("http://192.0.2.61", "https://evil.example/api") is None
    assert https_target("http://192.0.2.61", "http://192.0.2.61:8080/") is None
    assert https_target("https://192.0.2.61", "https://192.0.2.61:8443/") is None
    assert https_target("http://192.0.2.61", "/relative") is None


@pytest.mark.asyncio
async def test_the_authority_is_asked_of_the_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ca_pem, _cert, _key = _authority(tmp_path)
    answers = iter([
        httpx.Response(404),
        httpx.Response(200, json={"https": False, "ca_pem": None}),
        httpx.Response(200, json={"https": False, "ca_pem": ca_pem}),
        httpx.Response(200, json={"https": True, "ca_pem": "not a certificate"}),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == server_trust.TRUST_PATH
        assert request.headers["authorization"] == "Bearer cred"
        return next(answers)

    trust = ServerTrust(_config(tmp_path, "http://backend", monkeypatch))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend") as client:
        assert await trust.refresh(client, "cred") is False  # a backend from before the question
        assert await trust.refresh(client, "cred") is False  # no authority of its own
        assert await trust.refresh(client, "cred") is True
        assert await trust.refresh(client, "cred") is False  # junk is not stored
    assert (tmp_path / "state" / server_trust.CA_FILE).read_text() == ca_pem
