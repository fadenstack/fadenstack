"""The gateway presents its certificate where an upstream wants one: proven against a real TLS server.

The server here does what a machine's model-TLS proxy does: it serves only
over TLS, and only to a client with a certificate from its CA made for
client use. LiteLLM cannot be told a client certificate per call (its
OpenAI-compatible path builds its HTTP client from global settings), so the
gateway hands it a client made with the certificate; these tests show the
certificate actually arrives -- on the direct vLLM stream, through LiteLLM,
and for a provider configured with its own CA and certificate.
"""

from __future__ import annotations

import ipaddress
import json
import ssl
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway import upstream_tls
from llm_port_api.services.gateway.llm_adapter import LLMAdapter

NOW = datetime.now(UTC)


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _key_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()


@dataclass
class _Issued:
    cert: str
    key: str


class _CA:
    def __init__(self, name: str) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        self.cert = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(minutes=5)).not_valid_after(NOW + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )
        self.pem = _pem(self.cert)

    def issue(self, cn: str, usage: x509.ObjectIdentifier, *, ip: str | None = None) -> _Issued:
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(self.cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(minutes=5)).not_valid_after(NOW + timedelta(days=10))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False,
            )
        )
        if ip:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False,
            )
        return _Issued(cert=_pem(builder.sign(self.key, hashes.SHA256())), key=_key_pem(key))


def _chunk(content: str | None = None, **extra: Any) -> bytes:
    body: dict[str, Any] = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "qwen", **extra}
    if content is not None:
        body["choices"] = [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    return f"data: {json.dumps(body)}\n\n".encode()


class _Model(BaseHTTPRequestHandler):
    """An OpenAI-compatible engine that records who called it."""

    callers: list[str]

    def log_message(self, *_: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        peer = self.connection.getpeercert()  # type: ignore[attr-defined]
        cn = next((v for rdn in peer.get("subject", ()) for k, v in rdn if k == "commonName"), "")
        self.callers.append(cn)
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if self.path.endswith("/embeddings"):
            out = {"object": "list", "model": "e", "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.25]}],
                   "usage": {"prompt_tokens": 1, "total_tokens": 1}}
            self._json(out)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for part in (_chunk("he"), _chunk("llo"), _chunk(choices=[], usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})):
                self.wfile.write(part)
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({
            "id": "c", "object": "chat.completion", "created": 1, "model": "qwen",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        })

    def _json(self, out: dict[str, Any]) -> None:
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@dataclass
class _Machine:
    base: str
    ca: _CA
    callers: list[str]


@pytest.fixture
def machine() -> Iterator[_Machine]:
    """A model behind a proxy that, like nginx on the machines, wants a client certificate from its CA."""
    ca = _CA("machine CA")
    server_cert = ca.issue("10.0.0.1", ExtendedKeyUsageOID.SERVER_AUTH, ip="127.0.0.1")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    upstream_tls._load_chain(ctx, server_cert.cert, server_cert.key)  # noqa: SLF001 - same loader, both ends
    ctx.load_verify_locations(cadata=ca.pem)
    ctx.verify_mode = ssl.CERT_REQUIRED
    callers: list[str] = []
    handler = type("_Handler", (_Model,), {"callers": callers})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Machine(base=f"https://127.0.0.1:{server.server_address[1]}/app", ca=ca, callers=callers)
    finally:
        server.shutdown()
        server.server_close()


def _call(machine: _Machine, **changes: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "provider_type": ProviderType.VLLM,
        "base_url": machine.base,
        "api_key_encrypted": None,
        "litellm_provider": None,
        "litellm_model": None,
        "extra_params": None,
        "payload": {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]},
        "instance_id": uuid.uuid4(),
        "ssl_verify_mode": "machine",
    }
    call.update(changes)
    return call


@pytest.fixture
def gateway_identity(machine: _Machine) -> Iterator[None]:
    client = machine.ca.issue("fadenstack-gateway", ExtendedKeyUsageOID.CLIENT_AUTH)
    previous = upstream_tls.machine_identity.current
    upstream_tls.machine_identity.current = upstream_tls.UpstreamTls(
        trust_pem=machine.ca.pem, cert_pem=client.cert, key_pem=client.key,
    )
    try:
        yield
    finally:
        upstream_tls.machine_identity.current = previous


async def _text(stream: Any) -> str:
    out = []
    async for raw in stream:
        for line in raw.decode().splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                event = json.loads(line[6:])
                if "error" in event:
                    return "ERROR: " + event["error"]["message"]
                for choice in event.get("choices") or []:
                    out.append((choice.get("delta") or {}).get("content") or "")
    return "".join(out)


@pytest.mark.anyio
@pytest.mark.usefixtures("gateway_identity")
async def test_a_streamed_chat_goes_direct_and_presents_the_gateway_certificate(machine: _Machine) -> None:
    async with httpx.AsyncClient() as plain:
        adapter = LLMAdapter(http_client=plain)
        stream = await adapter.completion(**_call(machine), stream=True)
        assert await _text(stream) == "hello"
    assert machine.callers == ["fadenstack-gateway"]


@pytest.mark.anyio
@pytest.mark.usefixtures("gateway_identity")
async def test_through_litellm_the_certificate_arrives_too(machine: _Machine) -> None:
    adapter = LLMAdapter(http_client=None)
    result = await adapter.completion(**_call(machine))
    assert result.status_code == 200, result.payload  # type: ignore[union-attr]
    assert result.payload["choices"][0]["message"]["content"] == "hello"  # type: ignore[union-attr]
    embedded = await adapter.embedding(**{**_call(machine), "payload": {"model": "e", "input": "x"}})
    assert embedded.status_code == 200, embedded.payload
    assert machine.callers == ["fadenstack-gateway", "fadenstack-gateway"]


@pytest.mark.anyio
async def test_before_the_gateway_holds_its_certificate_it_says_so(machine: _Machine) -> None:
    previous = upstream_tls.machine_identity.current
    upstream_tls.machine_identity.current = None
    try:
        adapter = LLMAdapter(http_client=None)
        result = await adapter.completion(**_call(machine))
        assert result.status_code == 503  # type: ignore[union-attr]
        assert "machine certificate" in result.payload["error"]["message"]  # type: ignore[union-attr]
    finally:
        upstream_tls.machine_identity.current = previous
    assert machine.callers == []


@pytest.mark.anyio
async def test_a_machine_certificate_is_no_client(machine: _Machine) -> None:
    """A server-only certificate from the same CA -- another machine's -- does not get in."""
    other = machine.ca.issue("10.0.0.2", ExtendedKeyUsageOID.SERVER_AUTH, ip="10.0.0.2")
    previous = upstream_tls.machine_identity.current
    upstream_tls.machine_identity.current = upstream_tls.UpstreamTls(
        trust_pem=machine.ca.pem, cert_pem=other.cert, key_pem=other.key,
    )
    try:
        async with httpx.AsyncClient() as plain:
            stream = await LLMAdapter(http_client=plain).completion(**_call(machine), stream=True)
            assert (await _text(stream)).startswith("ERROR")
    finally:
        upstream_tls.machine_identity.current = previous
    assert machine.callers == []


@pytest.mark.anyio
async def test_a_provider_with_its_own_ca_and_certificate(machine: _Machine) -> None:
    """The per-provider settings, which LiteLLM ignored on this path before, now reach the connection."""
    client = machine.ca.issue("company-gateway", ExtendedKeyUsageOID.CLIENT_AUTH)
    adapter = LLMAdapter(http_client=None)
    own = _call(
        machine,
        provider_type=ProviderType.REMOTE_CUSTOM,
        ssl_verify_mode="verify_custom_ca",
        ssl_ca_bundle_pem=machine.ca.pem,
        ssl_client_cert_pem=client.cert,
        ssl_client_key_pem=client.key,
    )
    result = await adapter.completion(**own)
    assert result.status_code == 200, result.payload  # type: ignore[union-attr]
    assert machine.callers == ["company-gateway"]

    # Without the certificate the server refuses, which is what the tests above rely on.
    refused = await adapter.completion(**{**own, "ssl_client_cert_pem": None, "ssl_client_key_pem": None})
    assert refused.status_code >= 500  # type: ignore[union-attr]
    assert machine.callers == ["company-gateway"]


def test_the_setting_a_provider_asks_for() -> None:
    assert upstream_tls.for_provider(
        ssl_verify_mode=None, ssl_ca_bundle_pem=None, ssl_client_cert_pem=None, ssl_client_key_pem=None,
    ) is None
    tls = upstream_tls.for_provider(
        ssl_verify_mode="verify", ssl_ca_bundle_pem="CA", ssl_client_cert_pem=None, ssl_client_key_pem=None,
    )
    assert tls is not None and tls.trust_pem == "CA" and tls.cert_pem is None
    # Cert and key pasted as one PEM, as the provider form accepts.
    combined = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----\n..."
    tls = upstream_tls.for_provider(
        ssl_verify_mode="default", ssl_ca_bundle_pem=None, ssl_client_cert_pem=combined, ssl_client_key_pem=None,
    )
    assert tls is not None and tls.key_pem == combined
