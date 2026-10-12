"""Which site a browser request comes from, for the checks that keep another site's page from acting as the visitor.

The console signs in with a cookie, and a browser sends a cookie whoever's page made the request. ``SameSite=Lax``
holds back most cross-site posts, but it is one header of defence; the ``Origin`` a browser adds to every
state-changing request and to every WebSocket handshake says which site's page it was.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from faden_backend.settings import settings

#: The console's sign-in cookie (``cookie_transport`` in ``db/models/users.py``).
SIGN_IN_COOKIE = "fapiauth"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
        headers = Headers(scope=scope)
        if headers.get("authorization"):
            return False
        if SIGN_IN_COOKIE not in cookie_parser(headers.get("cookie", "")):
            return False
        claimed = headers.get("origin") or headers.get("referer")
        if claimed is None:
            return False
        return not origin_is_this_site(claimed, headers)
