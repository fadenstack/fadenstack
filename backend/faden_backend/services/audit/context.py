"""Who is asking, from where: what each audit event records about the request it came from.

:class:`AuditContextMiddleware` notes the client's address, its user agent and a request id for every
request; the console's sign-in dependency adds the person (:func:`remember_actor`). The audit writer reads
them from here, so no route has to pass them along.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RequestContext:
    """What an audit event says about the request that caused it."""

    client_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    #: ``console``, ``api`` (a token), ``cli`` or ``system``.
    source: str = "system"
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    #: An event was written for this request already: the request recorder adds none of its own.
    audited: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


_current: ContextVar[RequestContext | None] = ContextVar("faden_audit_request", default=None)


def current() -> RequestContext:
    """The running request's context; outside a request, an empty one (``source=system``)."""
    return _current.get() or RequestContext()


def begin(context: RequestContext) -> object:
    """Make *context* the current one; returns the token for :func:`end`."""
    return _current.set(context)


def end(token: object) -> None:
    """Put back the context from before :func:`begin`."""
    _current.reset(token)  # type: ignore[arg-type]


def remember_actor(user_id: uuid.UUID, email: str | None) -> None:
    """The signed-in person of the running request (set by the console's user dependency)."""
    context = _current.get()
    if context is not None:
        context.actor_id = user_id
        context.actor_email = email


def mark_audited() -> None:
    """The running request wrote an event of its own."""
    context = _current.get()
    if context is not None:
        context.audited = True


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")[:256]
    return None


class AuditContextMiddleware:
    """Notes address, user agent and request id of each HTTP request for the audit trail.

    The client's address is the one uvicorn gives: behind nginx, the real one (the proxy headers are trusted).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Note the request, run it, forget it."""
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        authorization = _header(scope, b"authorization") or ""
        context = RequestContext(
            client_ip=client[0] if client else None,
            user_agent=_header(scope, b"user-agent"),
            request_id=(_header(scope, b"x-request-id") or uuid.uuid4().hex)[:64],
            source="api" if authorization.lower().startswith("bearer ") else "console",
        )
        token = begin(context)
        try:
            await self.app(scope, receive, send)
        finally:
            end(token)
