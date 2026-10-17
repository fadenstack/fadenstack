"""Every privileged request in the trail, without each route having to remember it.

Runs once per request, when its response starts (:class:`faden_backend.db.dependencies.RequestBoundary`):

- **Writes** (POST, PUT, PATCH, DELETE) under the administration areas are recorded, refused ones too,
  unless the route wrote an event of its own (its event says more).
- **Reads of what belongs to other people or opens secrets** are recorded: the admin chat browser,
  observability, the log viewer, the audit trail itself, settings values, another user's tokens. Pages that
  poll are recorded once per person, view and ten minutes.
- Only what a signed-in person asked: machines and the platform's own services (no person) are not recorded
  here; a person's own chats and profile are not privileged and not recorded either.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from faden_backend.db.models.containers import AuditResult
from faden_backend.services.audit import chain
from faden_backend.services.audit import context as request_context

logger = logging.getLogger(__name__)

_WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Areas whose writes are privileged.
_WRITE_AREAS = (
    "/api/admin/", "/api/llm/", "/api/inference/", "/api/skills", "/api/agents", "/api/agent-tokens",
    "/api/users/",
)
#: Writes that are a person's own business, or recorded where they happen (sign-in, password change).
_NOT_PRIVILEGED = ("/api/users/me", "/api/auth/", "/api/chat")
#: Reads recorded: other people's things, or secrets.
_SENSITIVE_READS = (
    "/api/admin/chat/", "/api/admin/observability/", "/api/logs/", "/api/admin/audit",
    "/api/admin/system/settings/values",
)
#: Read routes (as templates) recorded as well.
_SENSITIVE_READ_ROUTES = frozenset({
    "/api/admin/users/{user_id}/api-tokens",
    "/api/admin/stacks/{stack_id}/revisions",
    "/api/admin/stacks/{stack_id}/diff",
})
#: Areas whose events are high severity: people, rights, sign-in, other people's chats, the system, the trail.
_HIGH = (
    "/api/admin/users", "/api/admin/groups", "/api/admin/auth-providers", "/api/admin/chat",
    "/api/admin/system", "/api/admin/root-mode", "/api/admin/audit", "/api/admin/tls",
)
_READ_THROTTLE_S = 600.0
_recent_reads: dict[tuple[Any, ...], float] = {}


def _template(request: Any) -> str:
    """The route as a template (``/api/admin/users/{user_id}/roles``): the path with its parameters named.

    FastAPI's route object knows only its path within its own router, so the template is made from the path.
    """
    params = {str(value): name for name, value in (getattr(request, "path_params", {}) or {}).items()}
    segments = request.url.path.split("/")
    return "/".join(f"{{{params[segment]}}}" if segment in params else segment for segment in segments)


def _wanted(method: str, path: str, template: str) -> bool:
    if method in _WRITES:
        return path.startswith(_WRITE_AREAS) and not path.startswith(_NOT_PRIVILEGED)
    if method == "GET":
        return path.startswith(_SENSITIVE_READS) or template in _SENSITIVE_READ_ROUTES
    return False


def _throttled(actor: Any, template: str, params: dict[str, Any]) -> bool:
    now = time.monotonic()
    key = (actor, template, tuple(sorted((k, str(v)) for k, v in params.items())))
    last = _recent_reads.get(key)
    if last is not None and now - last < _READ_THROTTLE_S:
        return True
    _recent_reads[key] = now
    if len(_recent_reads) > 10_000:  # forget the oldest half; a busy console does not grow this for ever
        for stale in sorted(_recent_reads, key=_recent_reads.__getitem__)[:5_000]:
            _recent_reads.pop(stale, None)
    return False


async def record_request(request: Any, status_code: int) -> None:
    """Record *request* if a person asked for something privileged; never raises."""
    try:
        ctx = request_context.current()
        if ctx.actor_id is None:
            return
        method = request.method.upper()
        path = request.url.path
        template = _template(request)
        if not _wanted(method, path, template):
            return
        if method in _WRITES and ctx.audited:
            return
        params = dict(getattr(request, "path_params", {}) or {})
        if method == "GET" and status_code < 400 and _throttled(ctx.actor_id, template, params):
            return
        query = {k: v for k, v in request.query_params.items() if k not in ("token", "password")}
        sessions = getattr(request.state, "db_sessions", None) or []
        if not chain.configured() and not sessions:
            return  # the test app: no trail of its own and no request session to write into
        await chain.append(
            chain.NewEvent(
                action=f"api.{method.lower()}",
                target_type="route",
                target_id=template,
                result=AuditResult.ALLOW if status_code < 400 else AuditResult.DENY,
                actor_id=ctx.actor_id,
                severity="high" if path.startswith(_HIGH) or method == "DELETE" else "normal",
                metadata_json=json.dumps(
                    {"path": path, "params": {k: str(v) for k, v in params.items()}, "query": query,
                     "status": status_code},
                    ensure_ascii=False,
                )[:8000],
            ),
            request_session=sessions[0] if sessions else None,
        )
    except Exception:
        logger.exception("A request could not be recorded in the audit trail")
