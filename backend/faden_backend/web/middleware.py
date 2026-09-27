"""What the backend does to every response, whatever route made it."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecureCookiesMiddleware:
    """Mark every cookie ``Secure`` when the request arrived over HTTPS.

    The ``cookie_secure`` setting is static, and neither value suits an
    install that can switch HTTPS on and off: ``True`` breaks login over
    plain HTTP (browsers drop Secure cookies there), ``False`` leaves the
    session cookie unprotected once TLS is on. The scheme of the request
    is the fact that matters, and behind nginx it is known: uvicorn trusts
    ``X-Forwarded-Proto`` from the compose network (``FORWARDED_ALLOW_IPS``
    in docker-compose.yaml). So the flag follows the scheme: an HTTPS
    request gets Secure cookies whatever the setting says, an HTTP request
    keeps the setting's value.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("scheme") != "https":
            await self.app(scope, receive, send)
            return

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, secured(value)) if name.lower() == b"set-cookie" else (name, value)
                    for name, value in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_secured)


def secured(cookie: bytes) -> bytes:
    """*cookie* (a ``Set-Cookie`` value) carrying the Secure attribute, once."""
    attributes = {part.strip().lower() for part in cookie.split(b";")}
    return cookie if b"secure" in attributes else cookie + b"; Secure"
