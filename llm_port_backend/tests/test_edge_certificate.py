"""The certificate HTTPS is served with, seen from the backend.

A private issuer the CLI published counts like the install's own authority;
the addresses a machine being added is shown come from the CLI's note of the
host (``edge.json``), not from this container's interfaces; and the dashboard
says so before the certificate runs out.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from llm_port_backend.services import edge_trust
from llm_port_backend.services.dashboard import attention
from llm_port_backend.settings import settings
from llm_port_backend.web.api import install as install_api

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _cert(subject: str, key: ec.EllipticCurvePrivateKey, issuer: str, signer: ec.EllipticCurvePrivateKey,
          *, ca: bool, until: datetime) -> x509.Certificate:
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(signer, hashes.SHA256())
    )


def _pem(*certs: x509.Certificate) -> bytes:
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


@pytest.fixture
def tls_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "tls_edge_material_dir", str(tmp_path))
    return tmp_path


def _serve(tls_dir: Path, *, until: datetime, private: bool = True) -> None:
    """A private root published in ca.crt, an intermediate in the chain, and the served certificate."""
    root_key, inter_key, leaf_key, other = (ec.generate_private_key(ec.SECP256R1()) for _ in range(4))
    root = _cert("Corp Root", root_key, "Corp Root", root_key, ca=True, until=NOW + timedelta(days=3650))
    inter = _cert("Corp Issuing", inter_key, "Corp Root", root_key, ca=True, until=NOW + timedelta(days=1000))
    signer, issuer = (inter_key, "Corp Issuing") if private else (other, "Public CA")
    leaf = _cert("console.corp", leaf_key, issuer, signer, ca=False, until=until)
    (tls_dir / "ca.crt").write_bytes(_pem(root))
    (tls_dir / "edge.crt").write_bytes(_pem(leaf))
    (tls_dir / "edge-chain.pem").write_bytes(_pem(inter) if private else b"")
    (tls_dir / "edge.key").write_bytes(
        leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    )


def test_a_private_issuer_behind_an_intermediate_needs_the_published_root(tls_dir: Path) -> None:
    _serve(tls_dir, until=NOW + timedelta(days=90))
    assert edge_trust.needs_ca()
    _serve(tls_dir, until=NOW + timedelta(days=90), private=False)
    assert not edge_trust.needs_ca()


def test_machines_are_shown_the_hosts_addresses_not_the_containers(
    tls_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tls_dir / "edge.json").write_text(json.dumps(
        {"hosts": ["llm-port", "10.250.10.61"], "http_port": 8080, "https_port": 8443, "renews": "daily"}
    ))
    assert edge_trust.host_urls() == ["http://10.250.10.61:8080", "http://llm-port:8080"], "an address first"
    _serve(tls_dir, until=NOW + timedelta(days=90))
    assert edge_trust.host_urls()[0] == "https://10.250.10.61:8443"
    # The interfaces are not asked at all: from a container they are the container's.
    monkeypatch.setattr(install_api.psutil, "net_if_addrs", lambda: pytest.fail("asked the interfaces"))
    assert install_api.reachable_origins()[0] == {"url": "https://10.250.10.61:8443", "interface": "host"}


def test_without_the_note_the_interfaces_are_still_asked(tls_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev stack runs the backend on the host, with no CLI to write a note."""
    asked: list[bool] = []
    monkeypatch.setattr(install_api.psutil, "net_if_addrs", lambda: asked.append(True) or {})
    install_api.reachable_origins()
    assert asked


@pytest.mark.parametrize(
    ("days", "renews", "expected"),
    [
        (60, False, None),
        (25, False, ("tls_certificate_expiring", "warning")),
        (25, True, None),  # renewed by itself 30 days before; not a problem yet
        (15, True, ("tls_certificate_expiring", "warning")),  # it should have been: the renewal fails
        (3, True, ("tls_certificate_expiring", "critical")),
        (-1, False, ("tls_certificate_expired", "critical")),
    ],
)
@pytest.mark.anyio()
async def test_the_dashboard_says_so_before_the_certificate_runs_out(
    tls_dir: Path, days: int, renews: bool, expected: tuple[str, str] | None
) -> None:
    _serve(tls_dir, until=NOW + timedelta(days=days, hours=1))
    (tls_dir / "edge.json").write_text(json.dumps(
        {"hosts": [], "http_port": 80, "https_port": 443, "renews": "daily" if renews else "by hand"}
    ))
    items = await attention.edge_certificate(SimpleNamespace(now=NOW))  # type: ignore[arg-type]
    if expected is None:
        assert items == []
    else:
        assert [(i.code, i.severity) for i in items] == [expected]
        assert items[0].params["renews"] is renews


@pytest.mark.anyio()
async def test_nothing_to_say_while_https_is_off(tls_dir: Path) -> None:
    assert await attention.edge_certificate(SimpleNamespace(now=NOW)) == []  # type: ignore[arg-type]
