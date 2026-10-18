"""TLS toward an upstream that needs more than the public roots: the machines, and providers with their own.

Two kinds of upstream need it:

* **the machines** -- models deployed on GPU machines are reached through each
  machine's model-TLS proxy (nginx), which accepts only a client certificate
  from the machine CA with the gateway's name. The backend keeps that CA and
  the gateway's certificate in its database (``machine_ca``,
  ``machine_tls_client``); :class:`MachineIdentity` reads them every minute,
  opening the key with the settings master key the gateway already holds for
  the JWT secret.
* **providers configured with their own trust or client certificate** (a
  company CA in front of a self-hosted OpenAI-compatible server, a provider
  that wants mTLS).

LiteLLM cannot be told either per call: its OpenAI-compatible path builds its
HTTP client from the global settings only, and a client certificate is a
global setting on every path. What it does take is a ready-made client --
``AsyncOpenAI`` for the OpenAI-compatible providers, ``AsyncHTTPHandler`` for
its own HTTP path -- so this module builds one ``ssl.SSLContext`` per TLS
setting and hands out clients made with it. The TLS itself stays OpenSSL's,
under httpx.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import ssl
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from faden_gateway.settings import settings

logger = logging.getLogger(__name__)

#: The mode the backend sets on a provider instance served behind a machine's model-TLS proxy.
MACHINE_MODE = "machine"
#: What the machine CA's derived key is for (``faden_backend.services.machine_tls.authority``).
_CLIENT_KEY_PURPOSE = "machine-tls-client-key"
_GATEWAY_CLIENT = "fadenstack-gateway"
#: Clients not used for this long, and no longer current, are closed.
_IDLE_CLOSE_SEC = 1800.0
#: What a caller is told while the gateway has no machine certificate yet: it passes within a minute.
MACHINE_TLS_NOT_LOADED = (
    "This model is served over TLS by its machine, and the gateway has not loaded "
    "its machine certificate yet (the backend makes it; the gateway reads it every minute)."
)


class MachineTlsUnavailableError(RuntimeError):
    """A model on a machine was asked for before the gateway holds its machine certificate."""


@dataclass(frozen=True)
class UpstreamTls:
    """How to verify an upstream, and what to present to it."""

    #: PEM of the authorities to trust; ``None`` means the default trust.
    trust_pem: str | None = None
    cert_pem: str | None = None
    key_pem: str | None = None
    insecure: bool = False

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for part in (self.trust_pem, self.cert_pem, self.key_pem, "insecure" if self.insecure else ""):
            h.update((part or "").encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()


def _default_trust() -> ssl.SSLContext:
    bundle = settings.tls_outbound_default_ca_bundle
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi comes with httpx
        return ssl.create_default_context()


def _load_chain(ctx: ssl.SSLContext, cert_pem: str, key_pem: str) -> None:
    """``load_cert_chain`` wants files: write both 0600, load, remove at once."""
    directory = Path(tempfile.mkdtemp(prefix="faden-upstream-tls-"))
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    try:
        for path, data in ((cert_path, cert_pem), (key_path, key_pem)):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            try:
                os.write(fd, data.encode("utf-8"))
            finally:
                os.close(fd)
        ctx.load_cert_chain(cert_path, key_path)
    finally:
        for path in (cert_path, key_path):
            with contextlib.suppress(OSError):
                path.unlink()
        with contextlib.suppress(OSError):
            directory.rmdir()


def ssl_context(tls: UpstreamTls) -> ssl.SSLContext | bool:
    """The context for *tls*; ``False`` for an allowed insecure provider."""
    if tls.insecure:
        return False
    ctx = ssl.create_default_context(cadata=tls.trust_pem) if tls.trust_pem else _default_trust()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if tls.cert_pem and tls.key_pem:
        _load_chain(ctx, tls.cert_pem, tls.key_pem)
    return ctx


def for_provider(
    *,
    ssl_verify_mode: str | None,
    ssl_ca_bundle_pem: str | None,
    ssl_client_cert_pem: str | None,
    ssl_client_key_pem: str | None,
) -> UpstreamTls | None:
    """The TLS a provider instance asks for; ``None`` for the default (nothing of its own)."""
    mode = (ssl_verify_mode or "default").lower()
    if mode == MACHINE_MODE:
        identity = machine_identity.current
        if identity is None:
            raise MachineTlsUnavailableError(MACHINE_TLS_NOT_LOADED)
        return identity
    insecure = mode == "insecure" and settings.tls_outbound_allow_insecure
    if mode == "insecure" and not insecure:
        logger.warning("A provider asks for no TLS verification, which is not allowed here; verifying.")
    trust = ssl_ca_bundle_pem if mode in ("verify", "verify_custom_ca") and ssl_ca_bundle_pem else None
    cert = ssl_client_cert_pem or None
    key = ssl_client_key_pem or None
    if cert and not key and "PRIVATE KEY-----" in cert:
        key = cert  # cert and key in one PEM, as the provider form accepts
    if not (insecure or trust or (cert and key)):
        return None
    return UpstreamTls(trust_pem=trust, cert_pem=cert, key_pem=key, insecure=insecure)


class _Entry:
    __slots__ = ("client", "used")

    def __init__(self, client: Any) -> None:
        self.client = client
        self.used = time.monotonic()


class UpstreamClients:
    """Clients made with one TLS setting each, reused until they fall idle."""

    def __init__(self) -> None:
        self._http: dict[str, _Entry] = {}
        self._openai: dict[tuple[str, str, str], _Entry] = {}
        self._handler: dict[str, _Entry] = {}
        self._contexts: dict[str, ssl.SSLContext | bool] = {}

    def _context(self, tls: UpstreamTls) -> ssl.SSLContext | bool:
        key = tls.digest
        ctx = self._contexts.get(key)
        if ctx is None:
            ctx = ssl_context(tls)
            self._contexts[key] = ctx
        return ctx

    def http(self, tls: UpstreamTls) -> httpx.AsyncClient:
        """An httpx client for *tls* (the direct vLLM stream)."""
        key = tls.digest
        entry = self._http.get(key)
        if entry is None:
            entry = _Entry(httpx.AsyncClient(
                verify=self._context(tls),
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=40),
                timeout=httpx.Timeout(
                    connect=10.0, read=settings.litellm_request_timeout, write=10.0, pool=10.0,
                ),
            ))
            self._http[key] = entry
        entry.used = time.monotonic()
        return entry.client

    def openai(self, tls: UpstreamTls, *, base_url: str, api_key: str | None) -> Any:
        """An ``AsyncOpenAI`` for LiteLLM's OpenAI-compatible path, over :meth:`http`'s client."""
        from openai import AsyncOpenAI  # noqa: PLC0415

        # Taken on every use, so the httpx client under it never looks idle.
        http = self.http(tls)
        # The key itself: it never leaves this process's memory, and the client under it holds it anyway.
        key = (tls.digest, base_url, api_key or "")
        entry = self._openai.get(key)
        if entry is None or getattr(entry.client, "_client", http) is not http:
            entry = _Entry(AsyncOpenAI(
                base_url=base_url, api_key=api_key or "EMPTY", http_client=http, max_retries=2,
            ))
            self._openai[key] = entry
        entry.used = time.monotonic()
        return entry.client

    def handler(self, tls: UpstreamTls) -> Any:
        """An ``AsyncHTTPHandler`` for LiteLLM's own HTTP path (rerank, the native providers)."""
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler  # noqa: PLC0415

        key = tls.digest
        entry = self._handler.get(key)
        if entry is None:
            entry = _Entry(AsyncHTTPHandler(
                ssl_verify=self._context(tls), timeout=settings.litellm_request_timeout,
            ))
            self._handler[key] = entry
        entry.used = time.monotonic()
        return entry.client

    async def prune(self, *, keep: set[str]) -> int:
        """Close clients idle for half an hour whose setting is not in *keep*."""
        cutoff = time.monotonic() - _IDLE_CLOSE_SEC
        closed = 0
        for key, entry in list(self._openai.items()):
            if key[0] not in keep and entry.used < cutoff:
                del self._openai[key]
        for store in (self._http, self._handler):
            for key, entry in list(store.items()):
                if key in keep or entry.used >= cutoff:
                    continue
                del store[key]
                self._contexts.pop(key, None)
                closer = getattr(entry.client, "aclose", None) or getattr(entry.client, "close", None)
                if closer is not None:
                    try:
                        result = closer()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception:  # closing an idle client is best effort
                        logger.debug("Closing an idle upstream client failed", exc_info=True)
                closed += 1
        return closed


clients = UpstreamClients()


def _unseal_client_key(token: str) -> str:
    from cryptography.fernet import Fernet, MultiFernet  # noqa: PLC0415

    from faden_gateway.db.crypto import derive_fernet  # noqa: PLC0415

    # The backend sealed it; while a rotation is unfinished it may have done so with the previous key.
    keys: list[Fernet] = [derive_fernet(settings.settings_master_key, _CLIENT_KEY_PURPOSE)]
    previous = settings.settings_master_key_previous
    if previous and previous != settings.settings_master_key:
        keys.append(derive_fernet(previous, _CLIENT_KEY_PURPOSE))
    return MultiFernet(keys).decrypt(token.encode("ascii")).decode("ascii")


async def _read_backend() -> tuple[list[str], Any] | None:
    """The machine CAs' PEMs and the gateway's row from the backend database; ``None`` when unreadable."""
    import asyncpg  # noqa: PLC0415

    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/{settings.backend_db_base}"
    )
    try:
        conn = await asyncpg.connect(dsn, timeout=10)
    except Exception as exc:
        logger.warning("Machine TLS: could not reach the backend database - %s", exc)
        return None
    try:
        cas = await conn.fetch(
            "SELECT cert_pem FROM machine_ca WHERE retired_at IS NULL AND not_after > now() "
            "ORDER BY created_at, id",
        )
        client = await conn.fetchrow(
            "SELECT cert_pem, key_sealed, not_after FROM machine_tls_client WHERE name = $1",
            _GATEWAY_CLIENT,
        )
    except Exception as exc:  # a backend before the machine CA has no such tables
        logger.debug("Machine TLS: nothing to read yet - %s", exc)
        return None
    finally:
        await conn.close()
    if not cas or client is None:
        return None
    return [str(r["cert_pem"]) for r in cas], client


class MachineIdentity:
    """The gateway's machine certificate and the machine CAs, as the backend keeps them."""

    def __init__(self) -> None:
        self.current: UpstreamTls | None = None
        self.not_after: Any = None

    async def refresh(self) -> bool:
        """Read them again; ``True`` when something changed. Never raises."""
        if not settings.backend_db_base or not settings.settings_master_key:
            return False
        found = await _read_backend()
        if found is None:
            return False
        cas, client = found
        try:
            key_pem = _unseal_client_key(str(client["key_sealed"]))
        except Exception:
            logger.error(
                "Machine TLS: the gateway's machine key cannot be opened - is "
                "FADEN_GATEWAY_SETTINGS_MASTER_KEY the backend's settings master key?",
            )
            return False
        trust = "".join(pem.rstrip("\n") + "\n" for pem in cas)
        fresh = UpstreamTls(trust_pem=trust, cert_pem=str(client["cert_pem"]), key_pem=key_pem)
        if self.current is not None and fresh.digest == self.current.digest:
            return False
        try:
            ssl_context(fresh)  # refuse material OpenSSL will not load; keep the last good
        except (ssl.SSLError, ValueError) as exc:
            logger.error("Machine TLS: the backend's machine certificate does not load - %s", exc)
            return False
        self.current = fresh
        self.not_after = client["not_after"]
        logger.info("Machine TLS: gateway certificate loaded, valid until %s", client["not_after"])
        return True


machine_identity = MachineIdentity()


async def refresh_loop(interval_sec: float = 60.0) -> None:
    """Keep the machine identity current and close clients nobody uses any more."""
    import asyncio  # noqa: PLC0415

    while True:
        try:
            await machine_identity.refresh()
            keep = {machine_identity.current.digest} if machine_identity.current is not None else set()
            await clients.prune(keep=keep)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Machine TLS refresh failed")
        await asyncio.sleep(interval_sec)
