"""Media URLs in a chat request: only ``data:`` and public addresses go on to the model.

A picture, a sound or a video in a message can be a URL, which the model
server fetches itself: vLLM does (it runs without ``--allowed-media-domains``),
and LiteLLM downloads some for the providers that take no URLs. A URL a client
sends would have the GPU machine, or this gateway, fetch whatever it names --
the instance metadata service, the admin API of a container next to it, any
address on the internal network.

So the media parts of the messages a client sends are checked before
anything else happens to the request: a ``data:`` URL always passes (nothing
is fetched); an http(s) URL passes when every address its host resolves to is
public, and is refused with a 400 otherwise -- loopback, private, link-local,
CGNAT, multicast, reserved, unspecified, the metadata addresses, or a host
that does not resolve from here (the model server may resolve it to something
internal). Every other scheme is refused.

What the gateway adds itself (an attached picture, as a ``data:`` URL) is
added after the check and never goes through it.

Settings: ``media_url_check`` turns the check off; ``media_url_allowed_hosts``
lets internal hosts or networks through (an install whose pictures live on
an internal server).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Iterator
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.settings import settings

#: How long a host may take to resolve; then it counts as not resolving.
DNS_TIMEOUT_SEC = 2.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Part types (and the keys) that carry media a server may fetch: OpenAI's, vLLM's, the Responses API's and
#: Anthropic's shapes. vLLM also takes a part with no ``type`` by its key.
MEDIA_KEYS = frozenset({
    "image_url", "input_image", "image",
    "audio_url", "input_audio", "audio",
    "video_url", "input_video", "video",
    "file", "input_file", "document",
})
#: Keys whose text is never a media source, inside a media part (a caption, a title).
_TEXT_KEYS = frozenset({"text", "type", "detail", "format", "title", "filename", "name", "media_type"})

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _is_media_part(part: dict[str, Any]) -> bool:
    return part.get("type") in MEDIA_KEYS or any(key in part for key in MEDIA_KEYS)


def _strings(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """Every string in *value* that may be a media source, with where it is."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            if key not in _TEXT_KEYS:
                yield from _strings(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            yield from _strings(inner, f"{path}[{i}]")


def media_sources(messages: Any) -> Iterator[tuple[str, str]]:
    """``(where, url)`` for each URL in the media parts of *messages*; plain base64 data is not one."""
    if not isinstance(messages, list):
        return
    for m, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        parts = [content] if isinstance(content, dict) else content
        if not isinstance(parts, list):
            continue
        for p, part in enumerate(parts):
            if not isinstance(part, dict) or not _is_media_part(part):
                continue
            for where, value in _strings(part, f"messages[{m}].content[{p}]"):
                text = value.strip()
                # Anything with a scheme, or that a lenient parser reads as a host (base64 has no ':').
                if _SCHEME.match(text) or text.startswith(("//", "\\\\")):
                    yield where, text


def _embedded_v4(address: IPAddress) -> ipaddress.IPv4Address | None:
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_public(address: str | IPAddress) -> bool:
    """Whether *address* is a public unicast address (and so is any IPv4 address it carries)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0]) if isinstance(address, str) else address
    except ValueError:
        return False
    for candidate in (ip, _embedded_v4(ip)):
        if candidate is None:
            continue
        if (
            not candidate.is_global
            or candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_unspecified
            or (isinstance(candidate, ipaddress.IPv4Address) and candidate in _CGNAT)
        ):
            return False
    return True


@lru_cache(maxsize=8)
def _allowed(spec: str) -> tuple[frozenset[str], tuple[str, ...], tuple[Network, ...]]:
    """``media_url_allowed_hosts``: host names, ``*.suffix`` patterns, and IP addresses or networks."""
    hosts: set[str] = set()
    suffixes: list[str] = []
    networks: list[Network] = []
    for raw in spec.replace(";", ",").split(","):
        entry = raw.strip().lower().rstrip(".")
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry.strip("[]"), strict=False))
            continue
        except ValueError:
            pass
        if entry.startswith("*."):
            suffixes.append(entry[1:])  # ".example.lan"
        else:
            hosts.add(entry)
    return frozenset(hosts), tuple(suffixes), tuple(networks)


def _host_allowed(host: str, spec: str) -> bool:
    hosts, suffixes, _networks = _allowed(spec)
    return host in hosts or any(host.endswith(suffix) for suffix in suffixes)


def _address_allowed(address: str, spec: str) -> bool:
    _hosts, _suffixes, networks = _allowed(spec)
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return any(ip.version == net.version and ip in net for net in networks)


async def _resolve(host: str) -> list[str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [host]
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=DNS_TIMEOUT_SEC,
        )
    except (OSError, TimeoutError, UnicodeError):
        return []
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


def _refusal(where: str, message: str) -> GatewayError:
    return GatewayError(
        status_code=400,
        message=message,
        error_type="invalid_request_error",
        param=where,
        code="media_url_not_allowed",
    )


async def check_url(where: str, url: str, *, allowed_hosts: str) -> None:
    """Raise a 400 :class:`GatewayError` unless *url* is a ``data:`` URL or a public http(s) one."""
    if url[:5].lower() == "data:":
        return
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        parts, host = None, ""
    # A backslash or a control character is read as a separator by some parsers and not by others: the host
    # checked here would not be the host fetched.
    odd = "\\" in url or any(ord(c) < 0x20 or ord(c) == 0x7F for c in url)
    if parts is None or parts.scheme.lower() not in ("http", "https") or not host or odd:
        raise _refusal(
            where,
            f"{where}: only data: URLs and public http(s) URLs are taken for pictures, audio and video.",
        )
    if _host_allowed(host, allowed_hosts):
        return
    addresses = await _resolve(host)
    if addresses and all(is_public(a) or _address_allowed(a, allowed_hosts) for a in addresses):
        return
    raise _refusal(
        where,
        f"{where}: {host} is not a public address that resolves from here, and media URLs must be. Send the "
        "file as a data: URL instead, or ask an administrator to allow the host "
        "(FADEN_GATEWAY_MEDIA_URL_ALLOWED_HOSTS).",
    )


async def check_messages(messages: Any) -> None:
    """Refuse a request whose messages carry a media URL that is not ``data:`` or public (see the module)."""
    if not settings.media_url_check:
        return
    seen: set[str] = set()
    for where, url in media_sources(messages):
        if url in seen:
            continue
        seen.add(url)
        await check_url(where, url, allowed_hosts=settings.media_url_allowed_hosts)
