"""The machine's model-TLS proxy: its certificate, its nginx configuration, its container.

The backend side (the machine CA, what it signs) is tested in the backend;
here a fake backend signs the agent's requests the same way, and a fake
container runtime records what the agent asks of it. The configuration is
checked for what makes the proxy safe: TLS on every published port, the
model on the loopback address behind it, client certificates required and
only the gateway's name let through. (``scripts``: a run of the real nginx
image with this configuration is part of the model-TLS end-to-end kit.)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from llm_port_node_agent import model_tls
from llm_port_node_agent.model_tls import ModelTls, ModelTlsError, render_conf

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class _Backend:
    """Answers /machine-tls and signs requests, as services/machine_tls does."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.issued = 0
        self.enabled = True
        self._new_ca()

    def _new_ca(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "machine CA")])
        self.ca = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(days=1)).not_valid_after(NOW + timedelta(days=1000))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )

    @property
    def key_id(self) -> str:
        return self.ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()

    @property
    def trust(self) -> str:
        return self.ca.public_bytes(serialization.Encoding.PEM).decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cred"
        if request.url.path == model_tls.INFO_PATH:
            if not self.enabled:
                return httpx.Response(200, json={"enabled": False})
            return httpx.Response(200, json={
                "enabled": True, "trust_pem": self.trust, "issuer_key_id": self.key_id,
                "names": self.names, "client_name": "fadenstack-gateway", "renew_before_sec": 10 * 86400,
            })
        csr = x509.load_pem_x509_csr(json.loads(request.content)["csr_pem"].encode())
        assert csr.is_signature_valid
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.names[0])]))
            .issuer_name(self.ca.subject).public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(minutes=5)).not_valid_after(NOW + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in self.names]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), critical=False)
            .sign(self.key, hashes.SHA256())
        )
        self.issued += 1
        return httpx.Response(200, json={
            "certificate_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "trust_pem": self.trust, "names": self.names, "not_after": cert.not_valid_after_utc.isoformat(),
        })


class _Runtime:
    """What the agent asks of Docker."""

    def __init__(self, *, images: dict[str, list[str]] | None = None) -> None:
        self.images = dict(images or {})
        self.containers: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []

    async def image_identity(self, ref: str) -> dict[str, Any]:
        layers = self.images.get(ref)
        return {"present": layers is not None, "rootfs_layers": layers or []}

    async def pull(self, ref: str, **_: Any) -> None:
        self.calls.append(("pull", ref))

    async def exists(self, name: str) -> bool:
        return name in self.containers

    async def inspect(self, name: str) -> dict[str, Any]:
        c = self.containers[name]
        return {"State": {"Running": c["running"]}, "Config": {"Image": c["image"]}}

    async def run(self, **kw: Any) -> str:
        self.calls.append(("run", kw))
        self.containers[kw["name"]] = {"running": True, "image": kw["image"]}
        return "cid"

    async def start(self, name: str) -> None:
        self.calls.append(("start", name))
        self.containers[name]["running"] = True

    async def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
        self.containers.pop(name, None)

    async def exec_(self, name: str, command: list[str], **_: Any) -> tuple[int, str, str]:
        self.calls.append(("exec", tuple(command)))
        return 0, "", ""

    async def logs(self, name: str, **_: Any) -> tuple[int, str]:
        return 0, self.log_text

    log_text = ""


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    layers = ["sha256:aaa", "sha256:bbb"]
    monkeypatch.setitem(model_tls.NGINX_ROOTFS, model_tls.machine_arch(), model_tls.rootfs_digest(layers))
    return layers


def _tls(tmp_path: Path, runtime: _Runtime, **kw: Any) -> ModelTls:
    tls = ModelTls(runtime=runtime, directory=tmp_path / "model-tls", now=lambda: NOW, **kw)
    tls.port_free = lambda _port: True
    tls.listening = lambda _port: True
    tls.settle_sec = 0.5
    return tls


def _http(backend: _Backend) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(backend.handler))


# ── configuration ────────────────────────────────────────────────────────


def test_the_configuration_puts_tls_and_the_gateway_check_in_front_of_loopback() -> None:
    conf = render_conf({8010: 28010, 8000: 28000}, client_name="fadenstack-gateway", ipv6=True)
    assert "listen 8010 ssl;" in conf and "listen [::]:8010 ssl;" in conf and "listen 8000 ssl;" in conf
    assert "proxy_pass http://127.0.0.1:28010;" in conf and "proxy_pass http://127.0.0.1:28000;" in conf
    assert "ssl_verify_client on;" in conf
    assert '"~(^|,)CN=fadenstack-gateway(,|$)" 1;' in conf
    assert conf.count("if ($llmport_client_ok = 0) { return 403; }") == 2
    assert "proxy_buffering off;" in conf, "streamed tokens must not wait in a buffer"
    assert "listen 8010;" not in conf, "no plain listener"
    assert "[::]" not in render_conf({8010: 28010}, client_name="g", ipv6=False)


# ── the certificate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_certificate_is_asked_for_once_and_renewed_for_a_reason(tmp_path: Path) -> None:
    backend = _Backend(["10.250.10.49"])
    tls = _tls(tmp_path, _Runtime())
    async with _http(backend) as http:
        assert await tls.refresh(http, "cred")
        assert backend.issued == 1 and tls.has_certificate()
        key = (tmp_path / "model-tls" / model_tls.KEY_FILE).read_bytes()
        assert not await tls.refresh(http, "cred"), "nothing changed, nothing asked"
        assert backend.issued == 1

        backend.names = ["10.250.10.50"]  # the machine got another address
        assert await tls.refresh(http, "cred")
        assert backend.issued == 2

        backend._new_ca()  # an administrator's rotation  # noqa: SLF001
        assert await tls.refresh(http, "cred")
        assert backend.issued == 3
        assert (tmp_path / "model-tls" / model_tls.TRUST_FILE).read_text() == backend.trust
    assert (tmp_path / "model-tls" / model_tls.KEY_FILE).read_bytes() == key, "the key stays; only certificates change"


@pytest.mark.asyncio
async def test_renewal_ten_days_before_the_end(tmp_path: Path) -> None:
    backend = _Backend(["m"])
    clock = [NOW]
    tls = ModelTls(runtime=_Runtime(), directory=tmp_path, now=lambda: clock[0])
    async with _http(backend) as http:
        await tls.refresh(http, "cred")
        clock[0] = NOW + timedelta(days=19)
        assert not await tls.refresh(http, "cred")
        clock[0] = NOW + timedelta(days=21)
        assert await tls.refresh(http, "cred")
    assert backend.issued == 2


@pytest.mark.asyncio
async def test_a_backend_without_it_leaves_everything_alone(tmp_path: Path) -> None:
    async with httpx.AsyncClient(base_url="http://b", transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        tls = _tls(tmp_path, _Runtime())
        assert not await tls.refresh(http, "cred")
        assert tls.enabled is False and not tls.has_certificate()
    backend = _Backend(["m"])
    backend.enabled = False
    async with _http(backend) as http:
        assert not await tls.refresh(http, "cred")
    assert backend.issued == 0


# ── the proxy ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_route_without_a_certificate(tmp_path: Path) -> None:
    with pytest.raises(ModelTlsError, match="no model-TLS certificate"):
        await _tls(tmp_path, _Runtime()).ensure_route(8010, 28010)


@pytest.mark.asyncio
async def test_the_proxy_starts_reloads_and_goes(tmp_path: Path, pinned: list[str]) -> None:
    runtime = _Runtime(images={model_tls.server_image_tag(): pinned})
    tls = _tls(tmp_path, runtime, host_directory="/host/model-tls")
    backend = _Backend(["10.250.10.49"])
    async with _http(backend) as http:
        await tls.refresh(http, "cred")
        # Asked for before the minute's round: the refresher fetches it now.
        assert await tls.ensure_route(8010, 28010) == {"protected": True, "public_port": 8010, "internal_port": 28010}
    run = next(c[1] for c in runtime.calls if c[0] == "run")
    assert run["image"] == model_tls.server_image_tag()
    assert run["volumes"] == ["/host/model-tls:/etc/llmport-model-tls:ro"]
    assert run["extra_args"] == ["--network", "host"]
    assert run["entrypoint"] == "nginx"
    assert json.loads((tmp_path / "model-tls" / model_tls.ROUTES_FILE).read_text()) == {"8010": 28010}

    runtime.calls.clear()
    await tls.ensure_route(8010, 28010)
    assert not [c for c in runtime.calls if c[0] in ("run", "exec")], "the same route changes nothing"

    await tls.ensure_route(8000, 28000)
    execs = [c[1] for c in runtime.calls if c[0] == "exec"]
    assert execs[0][:2] == ("nginx", "-t") and execs[1][-2:] == ("-s", "reload")

    await tls.remove_route(8000)
    await tls.remove_route(8010)
    assert model_tls.CONTAINER not in runtime.containers, "nothing left to front: the proxy goes"


@pytest.mark.asyncio
async def test_the_image_comes_from_the_server_then_the_registry(tmp_path: Path, pinned: list[str]) -> None:
    runtime = _Runtime()
    loads: list[dict[str, Any]] = []

    async def loader(**kw: Any) -> None:
        loads.append(kw)
        runtime.images[kw["image"]] = pinned  # the server's copy, as pinned

    tls = _tls(tmp_path, runtime, image_loader=loader)
    assert await tls._ensure_image() == model_tls.server_image_tag()  # noqa: SLF001
    assert loads == [{"image": model_tls.server_image_tag(), "expect_rootfs": model_tls.NGINX_ROOTFS[model_tls.machine_arch()]}]

    # No server copy: the registry, by digest.
    runtime2 = _Runtime()

    async def refusing(**_: Any) -> None:
        raise RuntimeError("404")

    async def pull(ref: str, **_: Any) -> None:
        runtime2.calls.append(("pull", ref))
        runtime2.images[ref] = pinned

    runtime2.pull = pull  # type: ignore[method-assign]
    tls2 = _tls(tmp_path / "b", runtime2, image_loader=refusing)
    assert await tls2._ensure_image() == f"nginx@{model_tls.NGINX_INDEX}"  # noqa: SLF001

    # A build other than the pinned one is refused, wherever it came from.
    runtime3 = _Runtime(images={model_tls.server_image_tag(): ["sha256:other"]})
    with pytest.raises(ModelTlsError, match="not available"):
        await _tls(tmp_path / "c", runtime3)._ensure_image()  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_port_another_program_holds_is_refused_and_not_kept(tmp_path: Path, pinned: list[str]) -> None:
    """Found on the DGX pair: an MCP server held 8100, nginx could not bind, the model was published there."""
    runtime = _Runtime(images={model_tls.server_image_tag(): pinned})
    tls = _tls(tmp_path, runtime)
    async with _http(_Backend(["10.250.10.49"])) as http:
        await tls.refresh(http, "cred")
    tls.port_free = lambda port: port != 8100
    # Asked before a model is started for it, too.
    assert "8100 is already in use" in (tls.port_problem(8100) or "")
    assert tls.port_problem(8110) is None
    with pytest.raises(ModelTlsError, match="8100 is already in use"):
        await tls.ensure_route(8100, 28100)
    assert tls.routes() == {} and not [c for c in runtime.calls if c[0] == "run"]
    # A port the proxy already serves is its own, not another program's.
    tls.port_free = lambda port: True
    await tls.ensure_route(8110, 28110)
    tls.port_free = lambda port: False
    assert tls.port_problem(8110) is None


@pytest.mark.asyncio
async def test_a_port_nginx_does_not_answer_on_is_rolled_back(tmp_path: Path, pinned: list[str]) -> None:
    runtime = _Runtime(images={model_tls.server_image_tag(): pinned})
    runtime.log_text = "2026/09/27 [emerg] 1#1: bind() to 0.0.0.0:8120 failed (98: Address in use)\n"
    tls = _tls(tmp_path, runtime)
    async with _http(_Backend(["10.250.10.49"])) as http:
        await tls.refresh(http, "cred")
    await tls.ensure_route(8010, 28010)
    tls.listening = lambda port: port != 8120  # the new one never answers
    with pytest.raises(ModelTlsError, match="does not serve port 8120.*bind"):
        await tls.ensure_route(8120, 28120)
    assert tls.routes() == {8010: 28010}, "the route that works stays; the one that does not goes"
    assert "listen 8120" not in (tmp_path / "model-tls" / model_tls.CONF_FILE).read_text()


def test_port_free_sees_a_listener() -> None:
    import socket

    with socket.socket() as held:
        held.bind(("0.0.0.0", 0))  # noqa: S104
        held.listen()
        port = held.getsockname()[1]
        assert not model_tls.port_free(port)
        assert model_tls.listening(port)
    assert model_tls.port_free(port)

