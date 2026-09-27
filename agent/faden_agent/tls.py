"""TLS helpers for node-agent HTTP and websocket clients.

The node-agent talks to:

* the backend HTTP API (``BackendClient`` — httpx)
* the backend websocket stream (``StreamClient`` — websockets)
* a Loki log endpoint (``LokiClient`` — httpx)

These helpers translate the agent's TLS settings (verify on/off,
optional CA bundle, optional mTLS client cert) into the right
arguments for each library.
"""

from __future__ import annotations

import ssl
from typing import Any

from llm_port_node_agent.config import AgentConfig


def httpx_verify(config: AgentConfig) -> Any:
    """Return value for ``httpx.AsyncClient(verify=...)``.

    The shared context (server_trust.build_context) when there is one: the
    same object the stream uses, so an authority learnt from the backend while
    running is trusted by the next connection of either.
    """
    if not config.verify_tls:
        return False
    if config.ssl_context is not None:
        return config.ssl_context
    if config.tls_ca_bundle:
        return config.tls_ca_bundle
    return True


def httpx_cert(config: AgentConfig) -> Any:
    """Return value for ``httpx.AsyncClient(cert=...)`` or ``None``."""
    if config.ssl_context is not None:
        return None  # the shared context carries the client certificate already
    if config.tls_client_cert and config.tls_client_key:
        return (config.tls_client_cert, config.tls_client_key)
    if config.tls_client_cert:
        return config.tls_client_cert
    return None


def websockets_ssl(config: AgentConfig) -> ssl.SSLContext | bool | None:
    """Build SSL parameter for ``websockets.connect(ssl=...)``.

    Returns ``None`` for plaintext (caller controls based on URL),
    ``False`` to disable verification, or a configured
    :class:`ssl.SSLContext` for verifying connections.
    """
    if not config.verify_tls:
        # websockets accepts an unverified context here.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if config.ssl_context is not None:
        return config.ssl_context
    ctx = ssl.create_default_context(cafile=config.tls_ca_bundle)
    if config.tls_client_cert and config.tls_client_key:
        ctx.load_cert_chain(config.tls_client_cert, config.tls_client_key)
    elif config.tls_client_cert:
        ctx.load_cert_chain(config.tls_client_cert)
    return ctx
