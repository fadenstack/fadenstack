"""The install's front door: its ports, and whether it answers over HTTPS.

nginx serves HTTPS whenever ``<install>/tls`` holds ``edge.crt`` and
``edge.key``, and sends plain HTTP there (shared/nginx/select-config.sh).
Everything the CLI does against the console after ``docker compose up`` --
the health gate, the first-admin bootstrap, a login for an enrollment token
-- has to follow, or it talks to a redirect.

The two host ports nginx is published on are ``FADEN_HTTP_PORT`` and
``FADEN_HTTPS_PORT`` in the install's ``.env``; docker-compose.yaml
publishes both, the HTTPS one even while nothing listens on it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

HTTP_PORT_KEY = "FADEN_HTTP_PORT"
HTTPS_PORT_KEY = "FADEN_HTTPS_PORT"
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443


def edge_material_present(install_dir: Path) -> bool:
    """Whether the install holds the certificate and key nginx serves HTTPS with."""
    tls = Path(install_dir) / "tls"
    return (tls / "edge.crt").is_file() and (tls / "edge.key").is_file()


def _port(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip().strip('"').strip("'")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def ports(env: Mapping[str, str]) -> tuple[int, int]:
    """``(http, https)``: the host ports nginx is published on, from the install's ``.env``."""
    return _port(env, HTTP_PORT_KEY, DEFAULT_HTTP_PORT), _port(env, HTTPS_PORT_KEY, DEFAULT_HTTPS_PORT)


def check_ports(http: int, https: int) -> str | None:
    """Why this pair of ports cannot be used, or ``None`` when it can."""
    for label, value in (("HTTP", http), ("HTTPS", https)):
        if not 1 <= value <= 65535:  # noqa: PLR2004
            return f"The {label} port must be between 1 and 65535, not {value}."
    if http == https:
        return f"HTTP and HTTPS cannot share port {http}."
    return None


def url_for(host: str, *, https: bool, env: Mapping[str, str]) -> str:
    """``http[s]://host[:port]`` for *host*, leaving out the scheme's default port."""
    http_port, https_port = ports(env)
    scheme, port, default = ("https", https_port, 443) if https else ("http", http_port, 80)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # an IPv6 address
    return f"{scheme}://{host}" if port == default else f"{scheme}://{host}:{port}"


def console_url(install_dir: Path, env: Mapping[str, str]) -> str:
    """``https://localhost[:port]`` with edge material in place, else ``http://localhost[:port]``.

    *env* is the install's ``.env``; the ports follow ``FADEN_HTTPS_PORT``
    and ``FADEN_HTTP_PORT`` the way docker-compose.yaml publishes them.
    """
    return url_for("localhost", https=edge_material_present(install_dir), env=env)


def _is_this_machine(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def verify_for(url: str) -> bool:
    """TLS verification for a console URL: off only when it is HTTPS to this machine.

    That certificate is usually made here, or issued for a name this machine
    does not call itself by; what the CLI needs to know is whether the
    services came up, and the box it runs on answers that. Any other host
    is verified: a password may be about to go to it.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        return True
    return not _is_this_machine(parts.hostname or "")
