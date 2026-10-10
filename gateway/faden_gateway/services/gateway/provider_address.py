"""Provider addresses the gateway does not send prompts to: link-local and cloud metadata.

The backend refuses such an endpoint when a provider is saved and when it
probes one (``faden_backend.services.outbound``). That is not enough on its
own: a host name can resolve to the metadata service later, and a row saved
before the check was never looked at. The gateway sends that address every
prompt and the provider's key, so it checks too, before a request is routed:
a provider whose host is, or resolves to, a refused address is left out of
the request's routes, and with none left the request ends in a 502
(``upstream_address_refused``).

Refused, as the backend refuses them: the link-local range, where the cloud
instance metadata services answer (``169.254.0.0/16``, ``fe80::/10``), the
other metadata addresses (``fd00:ec2::254`` on AWS, ``100.100.100.200`` on
Alibaba Cloud), and the "this host" addresses (``0.0.0.0/8``, ``::``).
Private ranges, loopback and ``host.docker.internal`` stay allowed: models on
the LAN, on this machine or in the next container are what Fadenstack is for.

Models Fadenstack runs on its machines (a cluster's deployments, a vLLM it
found there, a machine's runtime) are internal addresses by design and are not
checked. A host's answer is kept for :data:`CACHE_TTL_SEC`, so a request pays
for no lookup; a host that does not resolve is let through to fail by itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from faden_gateway.services.gateway.errors import GatewayError

logger = logging.getLogger(__name__)

#: How long a host's answer is kept.
CACHE_TTL_SEC = 60.0
#: How long a lookup may take; then the host counts as not resolving (and is let through).
DNS_TIMEOUT_SEC = 2.0
#: Where a provider instance comes from when Fadenstack runs it on one of its machines.
MACHINE_SOURCES = frozenset({"inference_deployment", "found_container"})
#: The TLS mode of an instance behind a machine's model-TLS proxy.
_MACHINE_TLS = "machine"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_METADATA = (
    ipaddress.ip_network("169.254.169.254/32"),  # AWS, Azure, GCP, Oracle, DigitalOcean, OpenStack
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS, IPv6
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud
)
_LINK_LOCAL = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
_THIS_HOST = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
#: An IPv4 address in an old spelling: ``2852039166``, ``0xa9fea9fe``, ``0251.0376.0251.0376``, ``127.1``.
_NUMERIC_V4 = re.compile(r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}$", re.IGNORECASE)

#: host -> (when the answer expires, why it is refused or None)
_cache: dict[str, tuple[float, str | None]] = {}
_CACHE_MAX = 1024


def _embedded_v4(address: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one carries (mapped, 6to4 or NAT64), if it carries one."""
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def refused_reason(address: str | IPAddress) -> str | None:
    """Why the gateway does not call *address*, or None when it may (the backend's rule)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0]) if isinstance(address, str) else address
    except ValueError:
        return None
    candidates: list[IPAddress] = [ip]
    if (inner := _embedded_v4(ip)) is not None:
        candidates.append(inner)
    for candidate in candidates:
        if any(candidate in net for net in _METADATA):
            return "a cloud metadata address"
        if any(candidate in net for net in _LINK_LOCAL):
            return "a link-local address"
        if any(candidate in net for net in _THIS_HOST):
            return "an unspecified address"
    return None


def _numeric_v4(host: str) -> str | None:
    """The address a numeric IPv4 spelling stands for, as Linux's resolver reads it."""
    if not _NUMERIC_V4.match(host):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def url_host(url: str | None) -> str | None:
    """The host a URL names, without brackets or a zone; None when it names none (``litellm://openai``)."""
    try:
        host = urlsplit((url or "").strip()).hostname
    except ValueError:
        return None
    return host.split("%", 1)[0].rstrip(".").lower() if host else None


async def resolve(host: str) -> list[str]:
    """Every address *host* resolves to; empty when it does not resolve in time."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=DNS_TIMEOUT_SEC,
        )
    except (OSError, TimeoutError, UnicodeError):
        return []
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


async def _lookup(host: str) -> str | None:
    """Why *host* is refused, looked up now; None when it is not."""
    reason = refused_reason(host)
    if reason is not None:
        return f"{host} is {reason}"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    numeric = _numeric_v4(host)
    for address in [numeric] if numeric is not None else await resolve(host):
        reason = refused_reason(address)
        if reason is not None:
            return f"{host} ({address}) is {reason}"
    return None


def _remember(host: str, reason: str | None, now: float) -> None:
    if len(_cache) >= _CACHE_MAX:
        for key in [key for key, (expires, _) in _cache.items() if expires <= now] or list(_cache):
            del _cache[key]
    _cache[host] = (now + CACHE_TTL_SEC, reason)


async def refusal(base_url: str | None) -> str | None:
    """Why the gateway does not call *base_url*, or None when it may; a host's answer is kept a minute."""
    host = url_host(base_url)
    if not host:
        return None
    now = time.monotonic()
    kept = _cache.get(host)
    if kept is not None and kept[0] > now:
        return kept[1]
    reason = await _lookup(host)
    _remember(host, reason, now)
    if reason is not None:
        logger.warning("A provider's address is refused: %s; the gateway sends it no requests", reason)
    return reason


def on_a_machine(candidate: Any) -> bool:
    """Whether *candidate* is a model Fadenstack runs on one of its machines (internal by design)."""
    return (
        getattr(candidate, "source_kind", None) in MACHINE_SOURCES
        or getattr(candidate, "node_id", None) is not None
        or (getattr(candidate, "ssl_verify_mode", None) or "").lower() == _MACHINE_TLS
    )


async def usable(candidates: Sequence[Any]) -> list[Any]:
    """*candidates* without the providers at a refused address; a 502 when that leaves none."""
    kept: list[Any] = []
    first: str | None = None
    for candidate in candidates:
        reason = None if on_a_machine(candidate) else await refusal(getattr(candidate, "base_url", None))
        if reason is None:
            kept.append(candidate)
        elif first is None:
            first = reason
    if first is not None and not kept:
        raise GatewayError(
            status_code=502,
            message=(
                f"The model's provider is at an address the gateway does not call: {first}. It sends no "
                "requests to link-local or cloud metadata addresses; an administrator corrects the "
                "provider's endpoint."
            ),
            error_type="server_error",
            code="upstream_address_refused",
        )
    return kept


def forget() -> None:
    """Drop every kept answer (tests, and a changed resolver)."""
    _cache.clear()
