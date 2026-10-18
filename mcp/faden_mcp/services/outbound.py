"""Addresses the MCP service does not call: link-local and cloud metadata.

The backend refuses such a server URL when one is registered or changed
(``faden_backend.services.outbound``). That is not enough on its own: a host
name can resolve to the metadata service later, a row saved before the check
was never looked at, and the settings proxy hands what a server answers to
the console. So the MCP service checks too, before it connects to an HTTP
server and before it asks one for its settings.

Refused, as the backend refuses them: the link-local range, where the cloud
instance metadata services answer (``169.254.0.0/16``, ``fe80::/10``), the
other metadata addresses (``fd00:ec2::254`` on AWS, ``100.100.100.200`` on
Alibaba Cloud), and the "this host" addresses (``0.0.0.0/8``, ``::``).
Loopback, private ranges and container names stay allowed: that is where MCP
servers run. A host that does not resolve is let through to fail by itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

#: How long a lookup may take; then the host counts as not resolving.
DNS_TIMEOUT_SEC = 2.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_METADATA = (
    # AWS, Azure, GCP, Oracle, DigitalOcean, OpenStack
    ipaddress.ip_network("169.254.169.254/32"),
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
#: An IPv4 address in an old spelling: ``2852039166``, ``0xa9fea9fe``,
#: ``0251.0376.0251.0376``, ``127.1``.
_NUMERIC_V4 = re.compile(
    r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+)){0,3}$", re.IGNORECASE,
)


def _embedded_v4(address: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 one carries (mapped, 6to4 or NAT64), if any."""
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def refused_reason(address: str) -> str | None:
    """Why the MCP service does not call *address*, or None when it may."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
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
    """The address a numeric IPv4 spelling stands for, as Linux reads it."""
    if not _NUMERIC_V4.match(host):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def url_host(url: str | None) -> str | None:
    """The host a URL names, without brackets or a zone; None for none."""
    try:
        host = urlsplit((url or "").strip()).hostname
    except ValueError:
        return None
    return host.split("%", 1)[0].rstrip(".") if host else None


async def resolve(host: str) -> list[str]:
    """Every address *host* resolves to; empty when it does not in time."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=DNS_TIMEOUT_SEC,
        )
    except (OSError, TimeoutError, UnicodeError):
        return []
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


async def refusal(url: str | None) -> str | None:
    """Why the MCP service does not call *url*, or None when it may.

    A name is refused when any of its addresses is: a name that points at the
    metadata service is the metadata service.
    """
    host = url_host(url)
    if not host:
        return None
    if refused_reason(host):
        addresses = [host]
    elif (numeric := _numeric_v4(host)) is not None:
        addresses = [numeric]
    else:
        addresses = await resolve(host)
    for address in addresses:
        reason = refused_reason(address)
        if reason:
            shown = host if host == address else f"{host} ({address})"
            return (
                f"{shown} is {reason}; the MCP service does not call "
                "link-local or cloud metadata addresses."
            )
    return None
