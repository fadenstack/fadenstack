"""Which site a browser request comes from, for the checks that keep another site's page from acting as the visitor.

The console signs in with a cookie, and a browser sends a cookie whoever's page made the request. ``SameSite=Lax``
holds back most cross-site posts, but it is one header of defence; the ``Origin`` a browser adds to every
state-changing request and to every WebSocket handshake says which site's page it was.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from starlette.datastructures import Headers
from starlette.requests import Request, cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from faden_backend.services.sign_in.contract import KEY_PATTERN
from faden_backend.settings import settings

#: The console's sign-in cookie (``cookie_transport`` in ``db/models/users.py``).
SIGN_IN_COOKIE = "fapiauth"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class CrossSitePost:
    r"""A route another site's page posts to by design, and what protects it in place of the ``Origin``.

    *path* is matched against ``scope["path"]``, the value the router matches (the backend runs without a
    ``root_path``), whole: ``\A`` and ``\Z``, as ``$`` would also let a trailing newline through.
    """

    method: str
    path: re.Pattern[str]
    reason: str

    def matches(self, scope: Scope) -> bool:
        """Whether *scope* is this route, by its method and its whole path."""
        return scope["method"] == self.method and self.path.search(scope["path"]) is not None


# A sign-in provider's key as the sign-in contract has it, without its own anchors.
_PROVIDER_KEY = KEY_PATTERN.removeprefix("^").removesuffix("$")

#: The only requests that may carry the sign-in cookie from another site's page (as Grafana's
#: ``AddSafeEndpoint`` and Spring Security's ``ignoringRequestMatchers`` name theirs). A provider on the same
#: site as the console (``adfs.corp.example`` beside ``ai.corp.example``) gets the cookie sent along with its
#: posts (``SameSite=Lax`` holds back cross-site posts only), so without these its answers would be refused.
#: Neither route reads the cookie: an answer signs a person in or ends sign-ins on its own proof (ASVS 5.0
#: 3.5.1: an anti-forgery token in place of the Origin).
CROSS_SITE_POSTS: tuple[CrossSitePost, ...] = (
    CrossSitePost(
        "POST", re.compile(rf"\A/api/auth/sso/{_PROVIDER_KEY}/acs\Z"),
        "SAML's HTTP-POST binding: the provider's page posts the answer here. Its anti-forgery token is the "
        "signed binding cookie faden_sso, which only this browser got at the start and which must name the "
        "transaction the answer's RelayState carries, and the module's InResponseTo check (SP-initiated "
        "only); the module checks the assertion's signature and uses it once (ASVS 5.0 6.8.2, 6.8.3).",
    ),
    CrossSitePost(
        "POST", re.compile(rf"\A/api/auth/sso/{_PROVIDER_KEY}/sls\Z"),
        "SAML Single Logout over HTTP-POST: the provider's page posts its logout message here, usually while "
        "the person is signed in. The module accepts it only signed by the provider, with this server's "
        "logout address as its Destination, and once; the same message over HTTP-Redirect is a GET, which "
        "this check never stopped (ASVS 5.0 6.8.2, 7.4.1, 7.6.1).",
    ),
)

# A hostname, IPv4 address or bracketed IPv6 address, with an optional port.
_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$",
)


def public_origin(request: Request) -> str:
    """This backend as the caller reached it: ``scheme://host[:port]``.

    The ``Host`` header is the one nginx passes on as the client sent it, port included, and the scheme its
    ``X-Forwarded-Proto``. ``X-Forwarded-Host`` is not read: a client can put anything there. A host that is
    not a plain name or address with an optional port is not used either.
    """
    host = request.headers.get("host", "").strip()
    if not _HOST_RE.match(host):
        return str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    return f"{proto if proto in ('http', 'https') else request.url.scheme}://{host}"


def _site_of(url: str) -> str:
    """``host[:port]`` of *url* in lower case; empty when it names none (``null``, junk)."""
    try:
        return (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""


def origin_is_this_site(origin: str, headers: Mapping[str, str]) -> bool:
    """Whether *origin* (an ``Origin`` or ``Referer`` value) names the host the request came to.

    nginx passes ``Host`` on, so the host the browser used is the one the backend sees. Behind a dev proxy that
    rewrites ``Host``, the one it forwarded in ``X-Forwarded-Host`` counts too, and an install behind
    another proxy can name its sites in ``trusted_origins``.
    """
    site = _site_of(origin)
    if not site:
        return False
    forwarded = (headers.get("x-forwarded-host") or "").split(",")[0]
    hosts = {(headers.get("host") or "").strip().lower(), forwarded.strip().lower()}
    hosts.discard("")
    return site in hosts or site in {_site_of(o) for o in settings.trusted_origins}


class CsrfMiddleware:
    """Refuse a state-changing request that carries the sign-in cookie from another site's page.

    Only a request the cookie alone signs in is checked: one with an ``Authorization`` header is signed in by
    that token (an app, a script; a page of another site cannot add the header without a CORS preflight the
    backend never grants), so there is nothing to forge. Safe methods change nothing and pass.

    A browser sends ``Origin`` on every POST, PUT, PATCH and DELETE; ``Referer`` stands in when it is missing.
    A request with neither is not from a browser page (curl, a test, a server-side client) and passes: it
    would have to be given the cookie by hand anyway. This also covers the routes that read the request body as
    JSON whatever its ``Content-Type`` (the chat proxy), which a content-type rule alone would leave to the
    cookie: no cross-site page gets past the Origin, with any content type.

    The routes in ``CROSS_SITE_POSTS`` are not checked: each says why another site's page posts there and what
    protects it instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in _SAFE_METHODS and self._from_other_site(scope):
            response = JSONResponse(
                {"detail": "This request comes from another site and was refused."}, status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _from_other_site(scope: Scope) -> bool:
        if any(route.matches(scope) for route in CROSS_SITE_POSTS):
            return False
        headers = Headers(scope=scope)
        if headers.get("authorization"):
            return False
        if SIGN_IN_COOKIE not in cookie_parser(headers.get("cookie", "")):
            return False
        claimed = headers.get("origin") or headers.get("referer")
        if claimed is None:
            return False
        return not origin_is_this_site(claimed, headers)
