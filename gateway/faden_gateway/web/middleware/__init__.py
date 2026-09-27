"""HTTP middleware for the API."""

from faden_gateway.web.middleware.security import register_security_middleware

__all__ = ["register_security_middleware"]
