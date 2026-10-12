"""Machines learn the install's certificate authority from the backend.

The CLI makes the authority (``tls/ca.crt``) at deploy even while HTTPS is off,
so an agent holds it before a certificate it signed is served; a new machine
fetches it through the install line, which checks it against the fingerprint
the console shows.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.services import edge_trust
from faden_backend.settings import settings
from faden_backend.web.api import install as install_api
from faden_backend.web.api.node_files import views as node_files


def _cert(subject: str, key: ec.EllipticCurvePrivateKey, issuer: str, signer: ec.EllipticCurvePrivateKey) -> bytes:
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=subject == issuer, path_length=None), critical=True)
        .sign(signer, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def tls_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "tls_edge_material_dir", str(tmp_path))
    return tmp_path


def _install(tls_dir: Path, *, signed_by_ours: bool) -> bytes:
    """ca.crt from the install's authority, and edge.crt/edge.key signed by it or by someone else."""
    ca_key, leaf_key, other = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca_pem = _cert("Install CA", ca_key, "Install CA", ca_key)
    (tls_dir / "ca.crt").write_bytes(ca_pem)
    issuer, signer = ("Install CA", ca_key) if signed_by_ours else ("Public CA", other)
    (tls_dir / "edge.crt").write_bytes(_cert("backend", leaf_key, issuer, signer))
    (tls_dir / "edge.key").write_bytes(
        leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    )
    return ca_pem


def test_nothing_to_trust_on_a_plain_install(tls_dir: Path) -> None:
    assert not edge_trust.https_on()
    assert edge_trust.ca_pem() is None
    assert edge_trust.ca_sha256() is None
    assert not edge_trust.needs_ca()


def test_the_authority_is_offered_before_https_is_on(tls_dir: Path) -> None:
    ca_pem = _install(tls_dir, signed_by_ours=True)
    for name in ("edge.crt", "edge.key"):
        (tls_dir / name).unlink()
    assert not edge_trust.https_on()
    assert edge_trust.ca_pem() == ca_pem
    assert edge_trust.ca_sha256() == hashlib.sha256(ca_pem).hexdigest()
    assert not edge_trust.needs_ca(), "nothing is served yet"


def test_a_certificate_of_its_own_needs_the_authority_a_public_one_does_not(tls_dir: Path) -> None:
    _install(tls_dir, signed_by_ours=True)
    assert edge_trust.https_on()
    assert edge_trust.needs_ca()
    _install(tls_dir, signed_by_ours=False)
    assert edge_trust.https_on()
    assert not edge_trust.needs_ca()


@pytest.mark.anyio()
async def test_a_machine_asks_which_authority_to_keep(
    client: AsyncClient, fastapi_app: FastAPI, tls_dir: Path
) -> None:
    fastapi_app.dependency_overrides[node_files._authenticate_node] = lambda: object()
    try:
        empty = (await client.get("/api/node-files/server-trust")).json()
        ca_pem = _install(tls_dir, signed_by_ours=True)
        body = (await client.get("/api/node-files/server-trust")).json()
    finally:
        fastapi_app.dependency_overrides.pop(node_files._authenticate_node, None)
    assert empty == {"https": False, "ca_pem": None, "ca_sha256": None}
    assert body == {"https": True, "ca_pem": ca_pem.decode(), "ca_sha256": hashlib.sha256(ca_pem).hexdigest()}


@pytest.mark.anyio()
async def test_the_question_needs_a_machine_credential(client: AsyncClient, tls_dir: Path) -> None:
    _install(tls_dir, signed_by_ours=True)
    # 401 with a database; this app has none and says 503 first. Either way: nothing without one.
    r = await client.get("/api/node-files/server-trust")
    assert r.status_code in (401, 503)
    assert "ca_pem" not in r.text


@pytest.mark.anyio()
async def test_a_new_machine_gets_the_authority_and_the_console_its_fingerprint(
    client: AsyncClient, tls_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install_api, "reachable_origins", lambda: [])
    assert (await client.get("/api/install/ca.crt")).status_code == 404
    assert (await client.get("/api/install/address")).json()["ca_sha256"] is None

    ca_pem = _install(tls_dir, signed_by_ours=True)
    r = await client.get("/api/install/ca.crt")
    assert r.status_code == 200
    assert r.content == ca_pem
    assert (await client.get("/api/install/address")).json()["ca_sha256"] == hashlib.sha256(ca_pem).hexdigest()

    # An operator's certificate from a public issuer: nothing to pin.
    _install(tls_dir, signed_by_ours=False)
    assert (await client.get("/api/install/address")).json()["ca_sha256"] is None


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
def test_the_installer_refuses_an_authority_file_that_is_not_there(tmp_path: Path) -> None:
    script = tmp_path / "install.sh"
    script.write_text(install_api.render_installer("https://192.0.2.61"), encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["sh", str(script), "--join", "--ca-file", str(tmp_path / "missing.crt")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_the_installer_verifies_the_backend_and_hands_the_authority_on() -> None:
    script = install_api.render_installer("https://192.0.2.61")
    assert 'curl --cacert "$CA_FILE" "$@"' in script
    assert 'PLAN=$(backend_curl -fsSL "$BACKEND/api/install/plan' in script
    assert 'set -- --ca-file "$CA_FILE"' in script
    assert '$SUDO_E "$INSTALL_PATH" start $SCOPE "$@"' in script
