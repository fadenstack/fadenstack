"""The backend's service token lets it in; any other token does not.

It is compared in constant time (``hmac.compare_digest``), as the backend and
the other modules compare theirs: ``==`` stops at the first byte that differs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from faden_mcp.settings import settings
from faden_mcp.web.api import auth

TOKEN = "s3rvice-t0ken"  # noqa: S105 - a test value


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service token and a JWT secret of the test's own."""
    monkeypatch.setattr(settings, "service_token", TOKEN)
    monkeypatch.setattr(settings, "jwt_secret", "a jwt secret of the test's own")


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _request(token: str) -> SimpleNamespace:
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_the_service_token_is_the_backend() -> None:
    """On the admin routes and the internal ones."""
    context = await auth.get_auth_context(_bearer(TOKEN))
    assert (context.user_id, context.tenant_id) == ("service", "default")
    await auth.verify_service_token(_request(TOKEN))  # type: ignore[arg-type]


@pytest.mark.parametrize("token", [TOKEN[:-1], TOKEN + "x", "s3rvice-t0kën", ""])
@pytest.mark.asyncio
async def test_another_token_is_not(token: str) -> None:
    """Shorter, longer, not ASCII, or empty."""
    with pytest.raises(HTTPException) as refused:
        await auth.verify_service_token(_request(token))  # type: ignore[arg-type]
    assert refused.value.status_code == 403
    if token:
        with pytest.raises(HTTPException) as refused:
            await auth.get_auth_context(_bearer(token))
        assert refused.value.status_code == 401  # read as a JWT, and not one


@pytest.mark.asyncio
async def test_with_no_service_token_set_no_token_is_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty configured token matches nothing."""
    monkeypatch.setattr(settings, "service_token", "")
    with pytest.raises(HTTPException) as refused:
        await auth.get_auth_context(_bearer("anything"))
    assert refused.value.status_code == 401
