from __future__ import annotations

from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError


class GatewayError(Exception):
    """Typed error raised by gateway services."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        error_type: str = "invalid_request_error",
        param: str | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type
        self.param = param
        self.code = code
        #: Response headers, e.g. ``Retry-After`` on a rate limit.
        self.headers = headers or {}
        #: Top-level fields beside ``error``, e.g. ``faden`` on a governance refusal.
        self.extra = extra or {}
        #: Set once the request log has a row for it, so the route does not add another.
        self.logged = False


def error_response(
    *,
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build an OpenAI-compatible error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": error_type,
                "message": message,
                "param": param,
                "code": code,
            },
            **(extra or {}),
        },
        headers=headers,
    )


def validation_message(exc: ValidationError) -> str:
    """What was wrong with a request body, field by field: ``messages.0.role: Field required``.

    Not ``str(exc)``, which repeats the values sent -- a whole manifest, whatever a client put in a
    field -- and adds a link to pydantic's documentation per error.
    """
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" if e["loc"] else e["msg"]
        for e in exc.errors(include_url=False, include_input=False)
    )


#: What a 422 keeps of each problem: where, what was wrong, and its kind. Not ``input``, the value sent,
#: nor ``ctx``, which may quote it.
_PROBLEM_KEYS = ("type", "loc", "msg")


def unreadable_request(exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422 for a body or parameter that does not fit (``{"detail": [...]}``), without the values."""
    problems = [{key: error[key] for key in _PROBLEM_KEYS if key in error} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": problems})


def error_from(exc: GatewayError) -> JSONResponse:
    """The response for *exc*, with its headers and extra fields."""
    return error_response(
        status_code=exc.status_code,
        message=exc.message,
        error_type=exc.error_type,
        param=exc.param,
        code=exc.code,
        headers=exc.headers,
        extra=exc.extra,
    )
