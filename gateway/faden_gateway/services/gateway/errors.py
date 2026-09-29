from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


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
