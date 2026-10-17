from collections.abc import AsyncGenerator
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from taskiq import TaskiqDepends

logger = logging.getLogger(__name__)


async def get_db_session(
    request: Request = TaskiqDepends(),
) -> AsyncGenerator[AsyncSession]:
    """
    Create and get database session.

    The session is committed before the response is sent (see
    :func:`commit_before_responding`); the commit after ``yield`` only
    catches what a response wrote while it was being sent.

    :param request: current request.
    :yield: database session.
    """
    session: AsyncSession = request.app.state.db_session_factory()
    _remember(request, session)

    try:
        yield session
    except Exception:
        # On request errors, always rollback so a failed transaction does
        # not leak into dependency teardown and trigger noisy cascades.
        try:
            await session.rollback()
        except Exception:
            logger.exception("Database rollback failed during request error handling.")
        raise
    else:
        # Commit only when the request completed successfully.
        try:
            await session.commit()
        except Exception:
            # Best effort rollback after commit failure.
            try:
                await session.rollback()
            except Exception:
                logger.exception("Database rollback failed after commit failure.")
            raise
    finally:
        try:
            await session.close()
        except Exception:
            logger.exception("Database session close failed.")


def _remember(request: Any, session: AsyncSession) -> None:
    """Note the request's session, for :func:`commit_before_responding`."""
    try:
        state = request.state
    except Exception:  # noqa: BLE001 - a task's stand-in request has no state
        return
    sessions = getattr(state, "db_sessions", None)
    if sessions is None:
        state.db_sessions = [session]
    else:
        sessions.append(session)


def commit_before_responding(app: Any) -> None:
    """Commit each request's database session before its response is sent, and record privileged requests.

    FastAPI ends a ``yield`` dependency after the response has been sent,
    so :func:`get_db_session` committed once the client already had its
    answer: a 201 could stand for a write that then failed to commit, and a
    request sent right after (create, then open) could miss what the first
    one wrote. Declaring the dependency ``scope="function"`` would fix the
    order, but the login machinery (fastapi-users) holds the same session
    in a dependency FastAPI will not let depend on a function-scoped one.

    So the innermost middleware (:class:`RequestBoundary`) commits the request's sessions when the response
    starts, before its status line leaves; a failing commit turns the answer into a 500 instead of passing
    unnoticed. It used to wrap each route's handler, which FastAPI 0.141 no longer calls for included routers
    (it builds their routes anew), so nothing was committed early any more. The same place records privileged
    requests in the audit trail (services/audit/recorder.py), refused ones included. Call it once, last.
    """
    from starlette.middleware import Middleware  # noqa: PLC0415

    if any(m.cls is RequestBoundary for m in app.user_middleware):
        return
    # Appended, not added: innermost, inside every other middleware, outside FastAPI's exception handling.
    app.user_middleware.append(Middleware(RequestBoundary))


class RequestBoundary:
    """Commits a request's sessions as its response starts, and records the request when it is privileged."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Run the request; commit as its response starts; record it."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        from faden_backend.services.audit.recorder import record_request  # noqa: PLC0415

        state = scope.setdefault("state", {})
        status = {"code": 500, "started": False, "swallow": False}

        async def committing_send(message: dict[str, Any]) -> None:
            if status["swallow"]:
                return
            if message["type"] == "http.response.start" and not status["started"]:
                status["started"] = True
                status["code"] = int(message.get("status", 500))
                if status["code"] < 400:
                    try:
                        for session in state.get("db_sessions") or ():
                            await session.commit()
                    except Exception:
                        logger.exception("The request's writes could not be committed")
                        status["code"], status["swallow"] = 500, True
                        await send({
                            "type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json")],
                        })
                        await send({
                            "type": "http.response.body",
                            "body": b'{"detail":"The change could not be saved."}',
                        })
                        return
            await send(message)

        try:
            await self.app(scope, receive, committing_send)
        except Exception:
            await record_request(Request(scope), 500)
            raise
        await record_request(Request(scope), status["code"])
