"""What machines need to trust this backend once it serves HTTPS, and what the CLI says about the front door.

The install's ``tls`` directory (``settings.tls_edge_material_dir``, mounted
into this container) holds what nginx serves -- ``edge.crt``, ``edge.key``,
``edge-chain.pem`` -- and ``ca.crt``: what the machines are to trust, as the
CLI publishes it (the install's own authority, the one before a rotation while
still valid, and an operator's private issuer). The CLI makes the authority at
deploy and upgrade even while HTTPS is off, so the machines have learnt it
before a certificate it signed is ever served.

``edge.json`` is the CLI's note of this host's addresses and ports: this
container sees only its own interfaces.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography import x509

from llm_port_backend.settings import settings

CA_BASENAME = "ca.crt"
EDGE_INFO_BASENAME = "edge.json"
_MAX_PEM = 64 * 1024


def material_dir() -> Path:
    return Path(settings.tls_edge_material_dir)


def https_on() -> bool:
    """Whether nginx serves HTTPS: the certificate and its key are both in place."""
    base = material_dir()
    return (base / settings.tls_edge_cert_basename).is_file() and (base / settings.tls_edge_key_basename).is_file()


def ca_pem() -> bytes | None:
    """What the machines are to trust, exactly as the file holds it; ``None`` when there is nothing."""
    path = material_dir() / CA_BASENAME
    try:
        if not path.is_file() or path.stat().st_size > _MAX_PEM:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return data if b"-----BEGIN CERTIFICATE-----" in data else None


def ca_sha256() -> str | None:
    """The sha256 of that file, as ``sha256sum`` prints it for the downloaded copy."""
    data = ca_pem()
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _served() -> tuple[x509.Certificate, list[x509.Certificate]] | None:
    """The certificate nginx serves and the chain sent after it."""
    base = material_dir()
    try:
        leaf = x509.load_pem_x509_certificate((base / settings.tls_edge_cert_basename).read_bytes())
    except (OSError, ValueError):
        return None
    chain_path = base / settings.tls_edge_chain_basename
    try:
        chain = x509.load_pem_x509_certificates(chain_path.read_bytes()) if chain_path.is_file() else []
    except (OSError, ValueError):
        chain = []
    return leaf, chain


def _issued_by(cert: x509.Certificate, issuers: list[x509.Certificate]) -> bool:
    for issuer in issuers:
        try:
            cert.verify_directly_issued_by(issuer)
        except Exception:  # noqa: BLE001, S112 - InvalidSignature, another key type: not this one
            continue
        return True
    return False


def needs_ca() -> bool:
    """Whether a machine needs the published ``ca.crt`` to trust what nginx serves.

    True when the certificate -- or one of the chain sent after it -- was
    issued by something in that file: the install's own authority, or an
    operator's private issuer. A certificate from a public issuer is trusted
    as it is, and the install line stays the plain one.
    """
    data = ca_pem()
    if data is None or not https_on():
        return False
    served = _served()
    if served is None:
        return False
    try:
        authorities = x509.load_pem_x509_certificates(data)
    except ValueError:
        return False
    leaf, chain = served
    return any(_issued_by(cert, authorities) for cert in [leaf, *chain])


def served_until() -> datetime | None:
    """When the certificate nginx serves runs out; ``None`` while HTTPS is off."""
    if not https_on():
        return None
    served = _served()
    return served[0].not_valid_after_utc if served else None


def edge_info() -> dict[str, Any] | None:
    """The CLI's note of this host's addresses and ports (``edge.json``), when there is one."""
    try:
        data = json.loads((material_dir() / EDGE_INFO_BASENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
        return None
    return data


def host_urls() -> list[str]:
    """How other machines reach this backend, from ``edge.json``: the scheme it is served with now."""
    info = edge_info()
    if info is None:
        return []
    https = https_on()
    port = int(info.get("https_port" if https else "http_port") or (443 if https else 80))
    default = 443 if https else 80
    scheme = "https" if https else "http"
    urls = []
    # An address or a dotted name first: a bare host name resolves on the server, rarely elsewhere.
    for host in sorted(info["hosts"], key=lambda h: "." not in str(h) and ":" not in str(h)):
        name = f"[{host}]" if ":" in str(host) else str(host)
        urls.append(f"{scheme}://{name}" if port == default else f"{scheme}://{name}:{port}")
    return urls
