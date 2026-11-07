import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, UJSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from faden_mcp.log import configure_logging
from faden_mcp.settings import settings
from faden_mcp.web.api.router import api_router
from faden_mcp.web.lifespan import lifespan_setup

APP_ROOT = Path(__file__).parent.parent


def get_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_logging()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_sample_rate,
            environment=settings.environment,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.getLevelName(settings.log_level.value),
                    event_level=logging.ERROR,
                ),
                SqlalchemyIntegration(),
            ],
        )
    app = FastAPI(
        title="faden_mcp",
        version=metadata.version("faden_mcp"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        default_response_class=UJSONResponse,
    )

    app.include_router(router=api_router, prefix="/api")
    app.add_exception_handler(RequestValidationError, _unreadable_request)  # type: ignore[arg-type]

    static_dir = APP_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


#: What a 422 keeps of each problem: where, what was wrong, and its kind.
#: Not ``input``, the value sent (a server's headers or token, a tool's arguments),
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
