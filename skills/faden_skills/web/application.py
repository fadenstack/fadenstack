import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from faden_skills.log import configure_logging
from faden_skills.services.bundle import BundleError
from faden_skills.services.errors import SkillError
from faden_skills.settings import settings
from faden_skills.web.api.router import api_router
from faden_skills.web.lifespan import lifespan_setup

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
        title="faden_skills",
        version=metadata.version("faden_skills"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        # FastAPI's own JSON, as in PII: UJSONResponse is deprecated, and
        # the tests turn the warning into a failure.
    )

    app.include_router(router=api_router, prefix="/api")
    app.add_exception_handler(SkillError, _skill_error)  # type: ignore[arg-type]
    app.add_exception_handler(BundleError, _bundle_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _unreadable_request)  # type: ignore[arg-type]

    return app


#: What a 422 keeps of each problem: where, what was wrong, and its kind.
#: Not ``input``, the value sent (a skill's text, a bundle),
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


async def _skill_error(_request: Request, exc: SkillError) -> JSONResponse:
    """A refusal from the skills layer, with the status it stands for."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def _bundle_error(_request: Request, exc: BundleError) -> JSONResponse:
    """Content that is not a skill this service can store."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})
