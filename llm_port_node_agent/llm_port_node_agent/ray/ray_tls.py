"""Ray's mutual TLS between a cluster's machines: this machine's key and certificate for it.

With ``RAY_USE_TLS=1`` every Ray process presents one certificate -- as gRPC
server and as client -- and trusts only the cluster's CA. The backend keeps a
CA per cluster (``services/machine_tls/ray_ca.py``); the agent makes the key
here, has the backend sign it (the names are the backend's: the fabric
address, the machine's own, loopback) and puts the files where the runtime
container reads them, ``/etc/llmport-ray-tls``.

Ray reads them when a process starts and never again, so they are written
only when the runtime container is (re)created -- rewritten under a running
cluster, new connections would present a certificate the running servers do
not know. A cluster started again gets new ones.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

CERTIFICATE_PATH = "/api/node-files/ray-tls/certificate"
#: Where the runtime container sees the files.
MOUNT = "/etc/llmport-ray-tls"
KEY_FILE = "node.key"
CERT_FILE = "node.crt"
CA_FILE = "ca.pem"


def container_env() -> dict[str, str]:
    """What turns Ray's TLS on inside the runtime container."""
    return {
        "RAY_USE_TLS": "1",
        "RAY_TLS_SERVER_CERT": f"{MOUNT}/{CERT_FILE}",
        "RAY_TLS_SERVER_KEY": f"{MOUNT}/{KEY_FILE}",
        "RAY_TLS_CA_CERT": f"{MOUNT}/{CA_FILE}",
    }


@dataclass
class RayTlsMaterial:
    """For one container launch: where the files are, and how to have them made."""

    host_dir: str
    prepare: Callable[[], Awaitable[Any]]


def _write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:  # pragma: no cover - Windows
        pass
    os.replace(tmp, path)


class RayTls:
    """Issues this machine's Ray certificates, one directory per cluster."""

    def __init__(
        self,
        *,
        base_dir: Path,
        http: httpx.AsyncClient,
        credential: Callable[[], str | None],
    ) -> None:
        self._base = Path(base_dir)
        self._http = http
        self._credential = credential

    def dir_for(self, environment_id: str) -> Path:
        return self._base / environment_id

    def material(self, environment_id: str) -> RayTlsMaterial:
        return RayTlsMaterial(
            host_dir=str(self.dir_for(environment_id)),
            prepare=lambda: self.issue(environment_id),
        )

    async def issue(self, environment_id: str) -> dict[str, Any]:
        """A fresh key and certificate for this machine in *environment_id*'s cluster."""
        from cryptography import x509  # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
        from cryptography.x509.oid import NameOID  # noqa: PLC0415

        credential = self._credential()
        if not credential:
            raise RuntimeError("No credential to ask for a Ray TLS certificate with.")
        directory = self.dir_for(environment_id)
        directory.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ray-node")]))
            .sign(key, hashes.SHA256())
        )
        response = await self._http.post(
            CERTIFICATE_PATH,
            headers={"Authorization": f"Bearer {credential}"},
            json={
                "environment_id": environment_id,
                "csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            },
        )
        if response.status_code >= 300:
            raise RuntimeError(f"The backend refused a Ray TLS certificate ({response.status_code}): {response.text[:300]}")
        body = response.json()
        # The key readable by its owner only; the container reads it as root.
        _write(directory / KEY_FILE, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ), 0o600)
        _write(directory / CERT_FILE, str(body["certificate_pem"]).encode("ascii"), 0o644)
        _write(directory / CA_FILE, str(body["ca_pem"]).encode("ascii"), 0o644)
        return body
