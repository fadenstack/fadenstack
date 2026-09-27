"""Where this machine reaches its own console: plain HTTP, or HTTPS once edge material exists.

nginx serves HTTPS whenever ``<install>/tls`` holds ``edge.crt`` and
``edge.key``, and sends plain HTTP there (llm_port_shared/nginx/select-config.sh).
Everything the CLI does against the console after ``docker compose up`` --
the health gate, the first-admin bootstrap, a login for an enrollment token
-- has to follow, or it talks to a redirect.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def edge_material_present(install_dir: Path) -> bool:
    """Whether the install holds the certificate and key nginx serves HTTPS with."""
    tls = Path(install_dir) / "tls"
    return (tls / "edge.crt").is_file() and (tls / "edge.key").is_file()


def console_url(install_dir: Path, env: Mapping[str, str]) -> str:
    """``https://localhost[:port]`` with edge material in place, else ``http://localhost[:port]``.

    *env* is the install's ``.env``; the ports follow ``LLM_PORT_HTTPS_PORT``
    and ``LLM_PORT_HTTP_PORT`` the way docker-compose.yaml publishes them.
    """
    if edge_material_present(install_dir):
        return _local("https", env.get("LLM_PORT_HTTPS_PORT"), "443")
    return _local("http", env.get("LLM_PORT_HTTP_PORT"), "80")


def _local(scheme: str, port: str | None, default: str) -> str:
    port = (port or "").strip().strip('"').strip("'") or default
    return f"{scheme}://localhost" if port == default else f"{scheme}://localhost:{port}"


def verify_for(url: str) -> bool:
    """TLS verification for a console URL: off when it is HTTPS to this machine.

    The certificate is usually self-signed, or issued for a name this
    machine does not call itself by. What the CLI needs to know is whether
    the services came up, and the box it runs on answers that.
    """
    return not url.startswith("https://")
