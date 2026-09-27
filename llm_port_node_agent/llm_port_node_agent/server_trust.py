"""What this agent trusts the backend with, and following the backend to HTTPS.

A backend on HTTPS with a certificate from its own authority (``llmport tls
generate``) is one no operating system trusts. The agent learns that
authority from the backend itself, over the connection it already has, and
keeps it beside its state (:data:`CA_FILE`); from then on it is trusted next to
the public roots. The backend makes the authority when it is deployed, long
before anyone switches HTTPS on, so a machine holds it before it needs it.

When the switch happens, the plain-HTTP address only redirects. An agent on
``http://`` that is sent to ``https://`` on the same host checks the new
address against what it trusts, moves there, and remembers the move
(:data:`ADDRESS_FILE`) -- for the address it was configured with, so pointing
it somewhere else by hand (``configure --set BACKEND_URL=...``) wins again.

It never moves the other way. An agent on HTTPS that cannot connect keeps
trying HTTPS: falling back to plain HTTP by itself would be a downgrade anyone
between it and the backend could force by blocking one port.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from llm_port_node_agent.config import AgentConfig

log = logging.getLogger(__name__)

#: The backend's authority, as fetched from it; next to state.json.
CA_FILE = "server-ca.pem"
#: Where the agent moved itself: {"from": configured URL, "to": HTTPS URL, "at": ...}.
ADDRESS_FILE = "server-address.json"
TRUST_PATH = "/api/node-files/server-trust"
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_PEM = 64 * 1024


def state_dir(config: AgentConfig) -> Path:
    return Path(config.state_path).parent


def build_context(config: AgentConfig) -> ssl.SSLContext | None:
    """The TLS context every connection to the backend verifies with; ``None`` when verification is off.

    Public roots (certifi, as httpx uses, and the system's), the bundle the
    operator configured, and the authority fetched from the backend. One
    object, shared by the HTTP client and the stream, so an authority learnt
    while running counts for the next connection of either.
    """
    if not config.verify_tls:
        return None
    import certifi  # noqa: PLC0415

    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        ctx.load_default_certs()
    except ssl.SSLError:  # pragma: no cover - a broken system store is not ours to fix
        log.debug("System certificate store could not be read.", exc_info=True)
    if config.tls_ca_bundle:
        ctx.load_verify_locations(cafile=config.tls_ca_bundle)
    stored = state_dir(config) / CA_FILE
    if stored.is_file():
        try:
            ctx.load_verify_locations(cafile=str(stored))
        except (ssl.SSLError, OSError):
            log.warning("The stored backend authority %s cannot be read; ignoring it.", stored)
    if config.tls_client_cert:
        ctx.load_cert_chain(config.tls_client_cert, config.tls_client_key or None)
    return ctx


def forget_address(directory: Path) -> bool:
    """Drop a move to HTTPS the agent made by itself; ``True`` if there was one."""
    path = directory / ADDRESS_FILE
    if path.is_file():
        path.unlink()
        return True
    return False


def saved_address(directory: Path) -> dict[str, str] | None:
    """The move to HTTPS the agent made by itself, if any."""
    try:
        data = json.loads((directory / ADDRESS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("from"), str) and isinstance(data.get("to"), str):
        return data
    return None


def https_target(current: str, location: str) -> str | None:
    """The HTTPS base URL a redirect from *current* points at, if it may be followed.

    Only ``http://host[:port]`` to ``https://host[:port]`` on the same host:
    a redirect anywhere else is not the backend switching on TLS.
    """
    here, there = urlsplit(current), urlsplit(location)
    if here.scheme != "http" or there.scheme != "https":
        return None
    if not here.hostname or (there.hostname or "").lower() != here.hostname.lower():
        return None
    netloc = there.netloc.rsplit("@", 1)[-1]
    return f"https://{netloc}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ServerTrust:
    """The backend's authority and address, as this agent has learnt them."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._dir = state_dir(config)
        self.configured_url = config.backend_url
        stored = self._dir / CA_FILE
        self._ca_sha: str | None = _sha256(stored.read_bytes()) if stored.is_file() else None
        saved = saved_address(self._dir)
        if saved is not None and saved["from"] == self.configured_url:
            log.info("Using %s: this agent moved there from %s.", saved["to"], saved["from"])
            config.backend_url = saved["to"]

    @property
    def context(self) -> ssl.SSLContext | None:
        return self._config.ssl_context

    def verify(self) -> ssl.SSLContext | bool:
        """What httpx takes as ``verify``."""
        return self.context if self.context is not None else False

    def add_ca(self, pem: str) -> bool:
        """Trust *pem* from now on and keep it; ``True`` when it is new."""
        data = pem.encode("utf-8")
        digest = _sha256(data)
        if digest == self._ca_sha:
            return False
        if len(data) > _MAX_PEM or "-----BEGIN CERTIFICATE-----" not in pem:
            log.warning("The backend offered an authority that is not a PEM certificate; ignored.")
            return False
        if self.context is not None:
            try:
                self.context.load_verify_locations(cadata=pem)
            except ssl.SSLError:
                log.warning("The backend offered an authority that cannot be read; ignored.")
                return False
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._dir / (CA_FILE + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self._dir / CA_FILE)
        self._ca_sha = digest
        log.info("Stored the backend's certificate authority (sha256 %s).", digest[:16])
        return True

    async def refresh(self, client: httpx.AsyncClient, credential: str) -> bool:
        """Ask the backend which authority to trust; ``True`` when a new one was stored.

        A backend that predates the question answers 404: nothing to learn.
        """
        res = await client.get(TRUST_PATH, headers={"Authorization": f"Bearer {credential}"})
        if res.status_code == 404:
            return False
        res.raise_for_status()
        payload: Any = res.json()
        pem = payload.get("ca_pem") if isinstance(payload, dict) else None
        return self.add_ca(pem) if isinstance(pem, str) and pem.strip() else False

    async def follow_to_https(self) -> str | None:
        """Move to HTTPS when the plain-HTTP address sends us there; the new base URL, or ``None``.

        The new address has to answer over a connection verified against what
        this agent trusts; a certificate it cannot verify leaves it where it
        is, saying why.
        """
        current = self._config.backend_url
        if not current.startswith("http://"):
            return None
        timeout = self._config.request_timeout_sec
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as plain:
                res = await plain.get(f"{current}/api/health")
        except httpx.HTTPError:
            return None
        if res.status_code not in _REDIRECTS:
            return None
        target = https_target(current, res.headers.get("location", ""))
        if target is None:
            log.warning("The backend redirects to %s, which is not itself on HTTPS; not following.",
                        res.headers.get("location"))
            return None
        try:
            async with httpx.AsyncClient(verify=self.verify(), timeout=timeout) as secure:
                check = await secure.get(f"{target}/api/health")
        except httpx.ConnectError as exc:
            log.error(
                "The backend moved to %s, but its certificate is not one this machine trusts (%s). "
                "It learns the backend's own authority while connected; point it there by hand with "
                "llmport-agent configure --set BACKEND_URL=%s --set TLS_CA_BUNDLE=<the server's tls/ca.crt>.",
                target, exc, target,
            )
            return None
        except httpx.HTTPError:
            return None
        if check.status_code != 200:  # noqa: PLR2004
            return None
        self._remember(target)
        return target

    def _remember(self, target: str) -> None:
        previous = self._config.backend_url
        self._config.backend_url = target
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / ADDRESS_FILE).write_text(
            json.dumps({"from": self.configured_url, "to": target, "at": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
        log.warning("The backend moved to HTTPS: now using %s (was %s).", target, previous)
