import logging
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, UJSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from faden_backend.db.dependencies import commit_before_responding
from faden_backend.log import configure_logging
from faden_backend.services.audit.context import AuditContextMiddleware
from faden_backend.services.extensions import load_plugin
from faden_backend.services.system_settings.runtime_sync import FollowSettings
from faden_backend.settings import settings
from faden_backend.web.api.router import api_router
from faden_backend.web.lifespan import lifespan_setup
from faden_backend.web.middleware import SecureCookiesMiddleware
from faden_backend.web.origin import CsrfMiddleware

APP_ROOT = Path(__file__).parent.parent

# ── Optional EE plugin ────────────────────────────────────────────
try:
    from faden_ee.plugins.backend import backend_plugin  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    backend_plugin = None
# ──────────────────────────────────────────────────────────────────


def get_app() -> FastAPI:
    """
    Get FastAPI application.

    This is the main constructor of an application.

    :return: application.
    """
    configure_logging()
    if settings.sentry_dsn:
        # Enables sentry integration.
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_sample_rate,
            environment=settings.environment,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.getLevelName(
                        settings.log_level.value,
                    ),
                    event_level=logging.ERROR,
                ),
                SqlalchemyIntegration(),
            ],
        )
    app = FastAPI(
        title="faden-backend",
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        default_response_class=UJSONResponse,
    )
    # Settings changed in another worker are taken up before the request is handled.
    app.add_middleware(FollowSettings)
    # A body or parameter that does not fit is refused without repeating it (Enterprise's routes too).
    app.add_exception_handler(RequestValidationError, _unreadable_request)  # type: ignore[arg-type]
    # The session cookie is Secure whenever the page was served over HTTPS.
    app.add_middleware(SecureCookiesMiddleware)
    # A page of another site cannot act with the sign-in cookie.
    app.add_middleware(CsrfMiddleware)
    # Who asks from where, for the audit trail (services/audit/context.py).
    app.add_middleware(AuditContextMiddleware)

    # EE plugin routes are included FIRST so they shadow Core 402-gated
    # endpoints with enterprise implementations.
    if backend_plugin is not None:
        app.include_router(router=backend_plugin.router())
    # Its extensions to core (agent access rules, token lifetimes, ...), once.
    load_plugin(backend_plugin)

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    # Adds static directory.
    # This directory is used to access swagger files.
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    # Last, once every route exists: writes are committed before the answer.
    commit_before_responding(app)

    return app


#: What a 422 keeps of each problem: where, what was wrong, and its kind. Not ``input``, the value sent (a
#: password, a pasted key or certificate), nor ``ctx``, which may quote it.
_PROBLEM_KEYS = ("type", "loc", "msg")


async def _unreadable_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422 (``{"detail": [...]}``, as clients read it) without the values sent."""
    problems = [{key: error[key] for key in _PROBLEM_KEYS if key in error} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": problems})
