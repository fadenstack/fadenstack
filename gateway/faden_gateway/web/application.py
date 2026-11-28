import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from faden_gateway.log import configure_logging
from faden_gateway.services.gateway.errors import GatewayError, error_from, error_response, unreadable_request
from faden_gateway.settings import settings
from faden_gateway.web.api.router import api_router, public_router
from faden_gateway.web.lifespan import lifespan_setup

APP_ROOT = Path(__file__).parent.parent


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
            ],
        )
    app = FastAPI(
        title="faden_gateway",
        version=metadata.version("faden_gateway"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> Response:
        del request
        return error_from(exc)

    @app.exception_handler(PoolTimeoutError)
    async def database_busy_handler(request: Request, exc: PoolTimeoutError) -> Response:
        # Every database connection stayed taken for the pool's whole timeout: the gateway is over
        # capacity (a burst of long streamed requests, 2026-11-28), not broken. Say so, as 503.
        del request
        logging.getLogger(__name__).warning("Database pool exhausted: %s", exc)
        return error_response(
            status_code=503,
            message="The gateway is at capacity right now. Try again in a moment.",
            error_type="server_error",
            code="gateway_overloaded",
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(RequestValidationError)
    async def unreadable_request_handler(request: Request, exc: RequestValidationError) -> Response:
        del request
        return unreadable_request(exc)

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    # Public OpenAI-compatible gateway routes.
    app.include_router(router=public_router)
    # Adds static directory.
    # This directory is used to access swagger files.
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    return app
