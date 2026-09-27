"""What machines need to trust this backend once it serves HTTPS.

The install's ``tls`` directory (``settings.tls_edge_material_dir``, mounted
into this container) holds what nginx serves -- ``edge.crt`` and ``edge.key``
-- and, when the CLI made the install's own certificate authority, that
authority's certificate: ``ca.crt``. The CLI makes the authority at deploy
and upgrade even while HTTPS is off, so the machines have learnt it before a
certificate it signed is ever served.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature

from llm_port_backend.settings import settings

CA_BASENAME = "ca.crt"
_MAX_PEM = 64 * 1024


def material_dir() -> Path:
    return Path(settings.tls_edge_material_dir)


def https_on() -> bool:
    """Whether nginx serves HTTPS: the certificate and its key are both in place."""
    base = material_dir()
    return (base / settings.tls_edge_cert_basename).is_file() and (base / settings.tls_edge_key_basename).is_file()


def ca_pem() -> bytes | None:
    """The install's authority, exactly as the file holds it; ``None`` when there is none."""
    path = material_dir() / CA_BASENAME
    try:
        if not path.is_file() or path.stat().st_size > _MAX_PEM:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if b"-----BEGIN CERTIFICATE-----" in data else None


def ca_sha256() -> str | None:
    """The sha256 of the authority's file, as ``sha256sum`` prints it for the downloaded copy."""
    data = ca_pem()
    return hashlib.sha256(data).hexdigest() if data is not None else None


def served_by_our_ca() -> bool:
    """Whether the certificate nginx serves was signed by the install's authority.

    Only then does a machine need the authority to trust the backend; an
    operator's own certificate from a public issuer is trusted as it is.
    """
    data = ca_pem()
    if data is None or not https_on():
        return False
    try:
        served = x509.load_pem_x509_certificate((material_dir() / settings.tls_edge_cert_basename).read_bytes())
        authorities = x509.load_pem_x509_certificates(data)
    except (OSError, ValueError):
        return False
    for authority in authorities:
        try:
            served.verify_directly_issued_by(authority)
        except (ValueError, TypeError, InvalidSignature):
            continue
        return True
    return False
