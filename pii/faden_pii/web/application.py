import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from faden_pii.log import configure_logging
from faden_pii.settings import settings
from faden_pii.web.api.auth import verify_service_token
from faden_pii.web.api.router import api_router
from faden_pii.web.lifespan import lifespan_setup

APP_ROOT = Path(__file__).parent.parent

# ── Optional EE plugin ────────────────────────────────────────────
try:
    from faden_ee.plugins.pii import pii_plugin  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    pii_plugin = None
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
        title="faden_pii",
        version=metadata.version("faden_pii"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        # FastAPI's own JSON: it serializes a declared response model
        # directly, and warns that UJSONResponse is deprecated -- a warning
        # the tests turn into a failure.
    )
    from faden_pii.services.pii.errors import PIIError
    from faden_pii.services.pii.limits import IngressLimits

    async def pii_error_handler(request, error):
        return JSONResponse(
            {"detail": {"code": error.code}},
            status_code=error.status,
            headers={"Retry-After": "1"} if error.status == 503 else None,
        )

    app.add_exception_handler(PIIError, pii_error_handler)
    app.add_exception_handler(RequestValidationError, _unreadable_request)
    app.add_middleware(IngressLimits, settings=settings)

    # EE plugin routes are included FIRST so they shadow Core 402-gated
    # endpoints with enterprise implementations.
    if pii_plugin is not None:
        app.include_router(
            router=pii_plugin.router(), dependencies=[Depends(verify_service_token)]
        )

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    # Adds static directory.
    # This directory is used to access swagger files.
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    return app


#: What a 422 keeps of each problem: where, what was wrong, and its kind.
#: Not ``input``, the value sent (the very text to be scanned),
#: nor ``ctx``, which may quote it.
_PROBLEM_KEYS = ("type", "loc", "msg")


async def _unreadable_request(
    _request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """FastAPI's 422 (``{"detail": [...]}``) without the values sent."""
    problems = [
        {key: error[key] for key in _PROBLEM_KEYS if key in error}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": problems})
