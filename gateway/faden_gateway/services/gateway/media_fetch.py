"""Media a client names by URL is fetched by the gateway; the model gets it as a ``data:`` URL.

Checking a URL and then letting someone else fetch it does not hold: vLLM's
media connector on the GPU machine and LiteLLM's download each resolve the
host again and follow redirects, so a public URL answering
``302 http://169.254.169.254/...``, or a name whose DNS answer changes between
two lookups, still had an internal address fetched. Now nothing after the
gateway fetches a URL a client sent:

* each http(s) media URL of the client's messages is checked
  (:mod:`media_urls`) and fetched here, before the request goes any further;
* the connection goes only to an address the check allows: the host is
  resolved again when connecting, every address checked, the connection made
  to that address (TLS still verifies the host name), and the address the
  socket reached checked once more -- on the connection and on the response;
* redirects are not followed by the client: up to :data:`MAX_REDIRECTS` are
  followed here, each new URL checked as the first was;
* what comes back must be the part's kind (``image/*`` for a picture,
  ``audio/*``, ``video/*``; any file but a web page for a file part), at most
  ``media_url_max_bytes`` for the whole request, counted as it streams, within
  ``media_url_timeout_sec``;
* the model gets a ``data:`` URL in the URL's place (:func:`inline`), on every
  route -- streamed, whole, background turns, retries, the agents' -- because
  they all leave through the same step.

A refusal is a 400 in OpenAI's shape, the part as ``param``:
``media_url_not_allowed``, ``media_url_too_large``, ``media_url_unreachable``,
``media_url_unsupported_type``. With ``media_url_check`` off, nothing is
fetched and the URLs go on as they are, as they used to.

Hosts in ``media_url_allowed_hosts`` are fetched here too (the point is that
nothing downstream fetches); only their internal addresses are allowed.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import mimetypes
import ssl
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from faden_gateway.services.gateway import media_urls, upstream_tls
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.media_urls import MediaKey
from faden_gateway.settings import settings

logger = logging.getLogger(__name__)

#: Redirects followed for one media URL, at most; each new URL is checked as the first was.
MAX_REDIRECTS = 3
#: Media URLs of one request fetched at the same time.
_PARALLEL = 4
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: Types that say nothing about the file: its name's extension says more.
_GENERIC_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream", "application/binary"})
#: Not a file a client attaches but the page about it (a share link, a sign-in page).
_PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_ACCEPT = {"image": "image/*", "audio": "audio/*", "video": "video/*", "file": "*/*"}
_WHAT = {"image": "a picture", "audio": "audio", "video": "a video", "file": "a file"}
#: Bytes above which base64 encoding goes to a thread, not the event loop.
_ENCODE_IN_THREAD = 1 << 20


class MediaAddressRefusedError(Exception):
    """A connection a media fetch would make to, or made to, an address the policy refuses."""


def peer_allowed(host: str, peer: Any, spec: str) -> bool:
    """Whether the address a connection reached (a socket's ``server_addr``) may serve *host*'s media."""
    address = peer[0] if isinstance(peer, tuple) and peer else peer
    return isinstance(address, str) and media_urls.address_allowed(host, address, spec)


def _ipv4_first(addresses: Iterable[str]) -> list[str]:
    def order(address: str) -> tuple[int, str]:
        try:
            return ipaddress.ip_address(address).version, address
        except ValueError:
            return 99, address

    return sorted(addresses, key=order)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects only to addresses the media policy allows, resolved and checked at the moment of connecting.

    httpcore hands over the URL's host; it is resolved here, every address is
    checked, and the socket is opened to the address itself -- the name is
    not resolved again by anyone. TLS still verifies the certificate against
    the host name (httpcore takes it from the URL). The address the socket
    reached is checked once more.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        spec = settings.media_url_allowed_hosts
        addresses = _ipv4_first(await media_urls.resolve(host))
        if not addresses:
            raise MediaAddressRefusedError(f"{host} does not resolve from here")
        refused = [a for a in addresses if not media_urls.address_allowed(host, a, spec)]
        if refused:
            raise MediaAddressRefusedError(f"{host} resolves to {refused[0]}, which is not a public address")
        failed: Exception | None = None
        for address in addresses:
            try:
                stream = await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failed = exc
                continue
            peer = stream.get_extra_info("server_addr")
            if not peer_allowed(host, peer, spec):
                await stream.aclose()
                reached = peer[0] if isinstance(peer, tuple) and peer else "an unknown address"
                raise MediaAddressRefusedError(f"{host} was reached at {reached}, not at a public address")
            return stream
        assert failed is not None  # noqa: S101 - there was an address, and every one failed
        raise failed

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise MediaAddressRefusedError("media is not fetched over a local socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx's own transport over a connection pool whose connections go through :class:`PinnedBackend`.

    No connection is kept for another fetch: each one is checked when made.
    """

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        context = upstream_tls.ssl_context(upstream_tls.UpstreamTls())
        assert isinstance(context, ssl.SSLContext)  # noqa: S101 - the default trust is never "insecure"
        # httpx's transport takes no network backend; its pool does, so the pool is made here.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            max_connections=64,
            max_keepalive_connections=0,
            network_backend=backend or PinnedBackend(),
        )


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """A client for media fetches: no redirects followed, no proxy from the environment, no cookies kept."""
    return httpx.AsyncClient(
        transport=transport or _PinnedTransport(),
        follow_redirects=False,
        trust_env=False,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0),
        headers={"User-Agent": "Fadenstack-Gateway", "Accept-Encoding": "identity"},
    )


_SHARED: dict[str, httpx.AsyncClient] = {}


def client() -> httpx.AsyncClient:
    """The one client media is fetched with, made on first use."""
    shared = _SHARED.get("client")
    if shared is None or shared.is_closed:
        shared = _SHARED["client"] = make_client()
    return shared


async def aclose() -> None:
    """Close the shared client (the gateway's shutdown)."""
    shared = _SHARED.pop("client", None)
    if shared is not None:
        await shared.aclose()


@dataclass(slots=True)
class _Budget:
    """What a request's media URLs may still bring in, in bytes."""

    left: int


def _unreachable(where: str, message: str) -> GatewayError:
    return media_urls.refused(where, f"{where}: {message}.", code="media_url_unreachable")


def _too_large(where: str) -> GatewayError:
    limit = settings.media_url_max_bytes
    return media_urls.refused(
        where,
        f"{where}: the media this request names by URL is more than {limit / (1 << 20):g} MB in all, the "
        "most the gateway fetches for one request (FADEN_GATEWAY_MEDIA_URL_MAX_BYTES). Send smaller files.",
        code="media_url_too_large",
    )


def _media_type(declared: str | None, url: str) -> str:
    """The response's media type; for one that says nothing (octet-stream), what its name's extension says."""
    kind = (declared or "").split(";", 1)[0].strip().lower()
    if kind in _GENERIC_TYPES:
        guessed, _encoding = mimetypes.guess_type(urlsplit(url).path)
        kind = (guessed or kind or "application/octet-stream").lower()
    return kind


def _fits(kind: str, media_type: str) -> bool:
    if kind == "file":
        return media_type not in _PAGE_TYPES
    return media_type.startswith(f"{kind}/")


def _peer(response: httpx.Response) -> Any:
    stream = response.extensions.get("network_stream")
    return stream.get_extra_info("server_addr") if stream is not None else None


@asynccontextmanager
async def _get(where: str, url: str, kind: str) -> AsyncIterator[httpx.Response]:
    """The response to a GET of *url*, its body not read yet; from an allowed address, or a 400."""
    host = media_urls.url_host(url)
    spec = settings.media_url_allowed_hosts
    try:
        async with client().stream("GET", url, headers={"Accept": _ACCEPT[kind]}) as response:
            peer = _peer(response)
            if not peer_allowed(host, peer, spec):
                logger.warning("A media URL's host %s answered from %r, which is not allowed", host, peer)
                raise media_urls.refused(
                    where,
                    f"{where}: {host} answered from an address that is not public, and media URLs must be "
                    f"answered from one. {media_urls.ADVICE}",
                )
            yield response
    except MediaAddressRefusedError as exc:
        # Where the name led is the log's (see media_urls.not_public).
        logger.warning("A media URL was refused when connecting: %s", exc)
        raise media_urls.refused(
            where, f"{where}: {media_urls.not_public(host)}. {media_urls.ADVICE}",
        ) from None
    except (httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
        raise _unreachable(where, f"{host} could not be reached ({type(exc).__name__})") from None


async def _encoded(data: bytes) -> str:
    if len(data) < _ENCODE_IN_THREAD:
        return base64.b64encode(data).decode("ascii")
    return await asyncio.to_thread(lambda: base64.b64encode(data).decode("ascii"))


async def _body(where: str, url: str, kind: str, response: httpx.Response, budget: _Budget) -> str:
    """The ``data:`` URL of a final response: a 200 of the part's kind, within the request's budget."""
    host = media_urls.url_host(url)
    if response.status_code != httpx.codes.OK:
        raise _unreachable(where, f"{host} answered {response.status_code} for it")
    media_type = _media_type(response.headers.get("content-type"), url)
    if not _fits(kind, media_type):
        raise media_urls.refused(
            where, f"{where}: {host} sent {media_type}, not {_WHAT[kind]}.",
            code="media_url_unsupported_type",
        )
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > budget.left:
        raise _too_large(where)
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        budget.left -= len(chunk)
        if budget.left < 0:
            raise _too_large(where)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise _unreachable(where, f"{host} sent an empty file")
    return f"data:{media_type};base64,{await _encoded(data)}"


async def _follow(where: str, url: str, kind: str, budget: _Budget) -> str:
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        reason = await media_urls.refusal(current, allowed_hosts=settings.media_url_allowed_hosts)
        if reason is not None:
            via = ""
            if hop:
                target = media_urls.url_host(current) or "an address that is not a URL"
                logger.warning("A media URL redirected to %s, which is refused", target)
                via = f"the URL redirects to {target}: "
            raise media_urls.refused(where, f"{where}: {via}{reason}. {media_urls.ADVICE}")
        async with _get(where, current, kind) as response:
            location = response.headers.get("location")
            if response.status_code in _REDIRECT_STATUSES and location:
                current = urljoin(current, location.strip())
                continue
            return await _body(where, current, kind, response, budget)
    raise _unreachable(where, f"the URL redirects more than {MAX_REDIRECTS} times")


async def fetch(where: str, url: str, kind: str, budget: _Budget | None = None) -> str:
    """The ``data:`` URL of the media at *url*, fetched by the gateway (see the module); else a 400."""
    budget = budget if budget is not None else _Budget(settings.media_url_max_bytes)
    limit = settings.media_url_timeout_sec
    try:
        async with asyncio.timeout(limit):
            return await _follow(where, url, kind, budget)
    except TimeoutError:
        host = media_urls.url_host(url)
        raise _unreachable(where, f"{host} did not send it within {limit:g} s") from None


async def _fetch_all(wanted: dict[MediaKey, str]) -> dict[MediaKey, str]:
    """Each wanted media URL as its ``data:`` URL, a few at a time, in one budget; else the first refusal."""
    budget = _Budget(settings.media_url_max_bytes)
    gate = asyncio.Semaphore(_PARALLEL)

    async def one(key: MediaKey, where: str) -> tuple[MediaKey, str]:
        async with gate:
            return key, await fetch(where, key[0], key[1], budget)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(key, where)) for key, where in wanted.items()]
    except ExceptionGroup as failed:
        # The first refusal is the answer; the other fetches were cancelled with it.
        refusal = next((exc for exc in failed.exceptions if isinstance(exc, GatewayError)), None)
        if refusal is None:
            raise
        raise refusal from None
    return dict(task.result() for task in tasks)


async def _fetched(wanted: dict[MediaKey, str]) -> dict[MediaKey, str]:
    """Every URL of *wanted* checked first -- one refused, none is fetched -- then fetched."""
    spec = settings.media_url_allowed_hosts
    for (url, _kind), where in wanted.items():
        await media_urls.check_url(where, url, allowed_hosts=spec)
    return await _fetch_all(wanted)


def needed(messages: Any) -> bool:
    """Whether *messages* name media by a URL the gateway fetches (none with ``media_url_check`` off)."""
    return settings.media_url_check and bool(media_urls.wanted(messages))


async def fetch_messages(messages: Any) -> dict[MediaKey, str]:
    """The ``data:`` URL for each http(s) media URL of *messages*: checked, then fetched; a refused one a 400.

    Empty, and nothing looked up, for messages without one, and with ``media_url_check`` off.
    """
    if not settings.media_url_check:
        return {}
    wanted = media_urls.wanted(messages)
    return await _fetched(wanted) if wanted else {}


async def inline(payload: dict[str, Any], fetched: dict[MediaKey, str]) -> dict[str, Any]:
    """*payload* with each media URL in its messages as the ``data:`` URL fetched for it.

    What leaves for the model: a URL that was not fetched before (none should
    be) is checked and fetched now, so no media URL goes on. With
    ``media_url_check`` off, *payload* goes as it is.
    """
    if not settings.media_url_check:
        return payload
    messages = payload.get("messages")
    missing = {key: where for key, where in media_urls.wanted(messages).items() if key not in fetched}
    if missing:
        fetched = {**fetched, **await _fetched(missing)}
    replaced = media_urls.replace_sources(messages, fetched)
    return payload if replaced is messages else {**payload, "messages": replaced}
