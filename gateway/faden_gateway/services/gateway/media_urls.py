"""Media URLs in a chat request: which ones may be fetched, and where they are in the messages.

A picture, a sound or a video in a message can be a URL. Sent on as it is, the
model server fetches it itself -- vLLM does (it runs without
``--allowed-media-domains`` and follows redirects), and LiteLLM downloads some
for the providers that take no URLs -- each resolving the host again. A URL a
client sends would have the GPU machine, or this gateway, fetch whatever it
names: the instance metadata service, the admin API of a container next to
it, any address on the internal network.

So the gateway fetches them itself (``media_fetch``) and the model gets a
``data:`` URL. This module is the policy and the walk through the messages:

* a ``data:`` URL is taken as it is (nothing is fetched);
* an http(s) URL may be fetched when every address its host resolves to is
  public; loopback, private, link-local, CGNAT, multicast, reserved,
  unspecified, the metadata addresses, or a host that does not resolve are
  refused with a 400 -- before the request goes any further, and again on
  every connection and redirect the fetch makes;
* every other scheme is refused.

What the gateway adds itself (an attached picture, as a ``data:`` URL) is
added after the check and is never fetched.

Settings: ``media_url_check`` turns all of it off (URLs go on as they are, as
they used to); ``media_url_allowed_hosts`` lets internal hosts or networks
through (an install whose pictures live on an internal server) -- their media
is fetched by the gateway too.
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

#: How long a host may take to resolve; then it counts as not resolving.
DNS_TIMEOUT_SEC = 2.0

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Part types (and the keys) that carry media a server may fetch, and what they carry: OpenAI's, vLLM's,
#: the Responses API's and Anthropic's shapes. vLLM also takes a part with no ``type`` by its key.
_KINDS = {
    "image_url": "image", "input_image": "image", "image": "image",
    "audio_url": "audio", "input_audio": "audio", "audio": "audio",
    "video_url": "video", "input_video": "video", "video": "video",
    "file": "file", "input_file": "file", "document": "file",
}
MEDIA_KEYS = frozenset(_KINDS)
#: Keys whose text is never a media source, inside a media part (a caption, a title).
_TEXT_KEYS = frozenset({"text", "type", "detail", "format", "title", "filename", "name", "media_type"})
#: Keys of a file part that name a file somewhere else; fetched, it goes in ``file_data``.
_FILE_REFERENCES = frozenset({"file_id", "file_url"})

ADVICE = (
    "Send the file as a data: URL instead, or ask an administrator to allow the host "
    "(FADEN_GATEWAY_MEDIA_URL_ALLOWED_HOSTS)."
)

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

#: A media URL to fetch, and the kind its part holds: ``(url, "image" | "audio" | "video" | "file")``.
MediaKey = tuple[str, str]


def _is_media_part(part: dict[str, Any]) -> bool:
    return part.get("type") in MEDIA_KEYS or any(key in part for key in MEDIA_KEYS)


def part_kind(part: dict[str, Any]) -> str:
    """What a media part holds: ``image``, ``audio``, ``video`` or ``file``."""
    kind = _KINDS.get(str(part.get("type")))
    if kind is not None:
        return kind
    return next((_KINDS[key] for key in _KINDS if key in part), "file")


def _is_source(text: str) -> bool:
    # Anything with a scheme, or that a lenient parser reads as a host (base64 has no ':').
    return bool(_SCHEME.match(text)) or text.startswith(("//", "\\\\"))


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


def _media_parts(messages: Any) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """``(message index, part index, part)`` for each media part of *messages*."""
    if not isinstance(messages, list):
        return
    for m, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        parts = [content] if isinstance(content, dict) else content
        if not isinstance(parts, list):
            continue
        for p, part in enumerate(parts):
            if isinstance(part, dict) and _is_media_part(part):
                yield m, p, part


def _sources(messages: Any) -> Iterator[tuple[str, str, str]]:
    """``(where, url, kind)`` for each URL in the media parts of *messages*."""
    for m, p, part in _media_parts(messages):
        kind = part_kind(part)
        for where, value in _strings(part, f"messages[{m}].content[{p}]"):
            text = value.strip()
            if _is_source(text):
                yield where, text, kind


def media_sources(messages: Any) -> Iterator[tuple[str, str]]:
    """``(where, url)`` for each URL in the media parts of *messages*; plain base64 data is not one."""
    for where, url, _kind in _sources(messages):
        yield where, url


def wanted(messages: Any) -> dict[MediaKey, str]:
    """The media URLs of *messages* that are not ``data:`` URLs, each with where it first is."""
    found: dict[MediaKey, str] = {}
    for where, url, kind in _sources(messages):
        if url[:5].lower() != "data:":
            found.setdefault((url, kind), where)
    return found


def _replaced(value: Any, kind: str, fetched: dict[MediaKey, str]) -> Any:
    """*value* with the URLs in *fetched* as their ``data:`` URLs; *value* itself when none is in it."""
    if isinstance(value, str):
        return fetched.get((value.strip(), kind), value)
    if isinstance(value, list):
        items = [_replaced(inner, kind, fetched) for inner in value]
        return value if all(a is b for a, b in zip(items, value, strict=True)) else items
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] | None = None
    for key, inner in value.items():
        new = inner if key in _TEXT_KEYS else _replaced(inner, kind, fetched)
        if new is inner:
            continue
        out = dict(value) if out is None else out
        if key in _FILE_REFERENCES and isinstance(new, str):
            # The file is here now: a file part carries its data in ``file_data``.
            del out[key]
            out["file_data"] = new
        else:
            out[key] = new
    return value if out is None else out


def replace_sources(messages: Any, fetched: dict[MediaKey, str]) -> Any:
    """*messages* with each fetched media URL as its ``data:`` URL; *messages* itself when nothing changes.

    Copied where something changes, never changed in place: the client's
    message objects are also what the chat keeps of the turn.
    """
    if not fetched or not isinstance(messages, list):
        return messages
    out: list[Any] | None = None
    for m, p, part in _media_parts(messages):
        new = _replaced(part, part_kind(part), fetched)
        if new is part:
            continue
        out = list(messages) if out is None else out
        message = out[m] if out[m] is not messages[m] else dict(messages[m])
        content = message["content"]
        if isinstance(content, dict):
            message["content"] = new
        else:
            content = list(content) if content is messages[m].get("content") else content
            content[p] = new
            message["content"] = content
        out[m] = message
    return messages if out is None else out


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


def host_allowed(host: str, spec: str) -> bool:
    """Whether *host* is one ``media_url_allowed_hosts`` names, by name or pattern."""
    hosts, suffixes, _networks = _allowed(spec)
    host = host.rstrip(".").lower()
    return host in hosts or any(host.endswith(suffix) for suffix in suffixes)


def _address_allowed(address: str, spec: str) -> bool:
    _hosts, _suffixes, networks = _allowed(spec)
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return any(ip.version == net.version and ip in net for net in networks)


def address_allowed(host: str, address: str | None, spec: str) -> bool:
    """Whether *host*'s media may come from *address*: a public one, an allowed network's or host's."""
    if not address:
        return False
    return host_allowed(host, spec) or is_public(address) or _address_allowed(address, spec)


async def resolve(host: str) -> list[str]:
    """Every address *host* resolves to (an IP address is its own); empty when it does not resolve in time."""
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


def url_host(url: str) -> str:
    """The host *url* names, lower case, without brackets or a trailing dot; empty when it names none."""
    try:
        return (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


async def refusal(url: str, *, allowed_hosts: str) -> str | None:
    """Why the gateway may not fetch *url*, or None when it may (an http(s) URL of a public host)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        parts, host = None, ""
    # A backslash or a control character is read as a separator by some parsers and not by others: the host
    # checked here would not be the host fetched.
    odd = "\\" in url or any(ord(c) < 0x20 or ord(c) == 0x7F for c in url)
    if parts is None or parts.scheme.lower() not in ("http", "https") or not host or odd:
        return "only data: URLs and public http(s) URLs are taken for pictures, audio and video"
    if host_allowed(host, allowed_hosts):
        return None
    addresses = await resolve(host)
    if addresses and all(address_allowed(host, a, allowed_hosts) for a in addresses):
        return None
    return f"{host} is not a public address that resolves from here, and media URLs must be"


def refused(where: str, message: str, code: str = "media_url_not_allowed") -> GatewayError:
    """The 400 a media URL is refused with, OpenAI's way: ``invalid_request_error``, *where* as the param."""
    return GatewayError(
        status_code=400,
        message=message,
        error_type="invalid_request_error",
        param=where,
        code=code,
    )


async def check_url(where: str, url: str, *, allowed_hosts: str) -> None:
    """Raise a 400 :class:`GatewayError` unless *url* is a ``data:`` URL or one the gateway may fetch."""
    if url[:5].lower() == "data:":
        return
    reason = await refusal(url, allowed_hosts=allowed_hosts)
    if reason is not None:
        raise refused(where, f"{where}: {reason}. {ADVICE}")
