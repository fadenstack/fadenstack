"""Who may use the plain /v1 routes: active users, and API tokens that were not revoked."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from faden_gateway.services.gateway.agents import AgentDirectory, AgentLookupError, ApiTokenRecord, UserFlags
from faden_gateway.services.gateway.auth import API_TOKEN_TYPE, check_caller
from faden_gateway.services.gateway.errors import GatewayError

USER = "8a0e6d0e-1d4f-4c55-9a43-0b7a8f1f0a01"
OTHER = "5b2f3c1d-7e8a-4b9c-8d0e-1f2a3b4c5d6e"
TOKEN_ID = "0f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"


def _app(
    users: dict[str, UserFlags] | None = None,
    tokens: dict[str, ApiTokenRecord] | None = None,
    *,
    fail: bool = False,
) -> Any:
    async def fetch_user(user_id: str) -> UserFlags | None:
        if fail:
            raise AgentLookupError("database down")
        return (users or {}).get(user_id)

    async def fetch_api_token(token_id: str) -> ApiTokenRecord | None:
        if fail:
            raise AgentLookupError("database down")
        return (tokens or {}).get(token_id)

    async def fetch_agent(key: tuple[str, str]) -> None:
        return None

    directory = AgentDirectory(fetch_agent=fetch_agent, fetch_user=fetch_user, fetch_api_token=fetch_api_token)
    return SimpleNamespace(state=SimpleNamespace(agent_directory=directory))


def _login(sub: str = USER) -> dict[str, Any]:
    now = int(time.time())
    return {"sub": sub, "aud": ["fastapi-users:auth"], "iat": now, "exp": now + 3600}


def _api(sub: str = USER, jti: str = TOKEN_ID) -> dict[str, Any]:
    now = int(time.time())
    return {"sub": sub, "tenant_id": "default", "email": "a@example.com", "iat": now, "jti": jti, "typ": API_TOKEN_TYPE}


ACTIVE = UserFlags(is_active=True, is_superuser=False)


async def _refused(app: Any, claims: dict[str, Any]) -> GatewayError:
    with pytest.raises(GatewayError) as caught:
        await check_caller(app, claims)
    return caught.value


@pytest.mark.anyio
async def test_an_active_user_with_a_login_token_passes() -> None:
    await check_caller(_app({USER: ACTIVE}), _login())


@pytest.mark.anyio
async def test_a_deactivated_or_deleted_user_is_refused() -> None:
    err = await _refused(_app({USER: UserFlags(is_active=False, is_superuser=False)}), _login())
    assert (err.status_code, err.code) == (403, "user_inactive")
    err = await _refused(_app({}), _login())
    assert (err.status_code, err.code) == (403, "user_inactive")


@pytest.mark.anyio
async def test_a_recorded_api_token_passes() -> None:
    await check_caller(_app({USER: ACTIVE}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False)}), _api())


@pytest.mark.anyio
async def test_a_revoked_unknown_or_borrowed_api_token_is_refused() -> None:
    users = {USER: ACTIVE, OTHER: ACTIVE}
    for tokens, claims in (
        ({TOKEN_ID: ApiTokenRecord(USER, revoked=True)}, _api()),
        ({}, _api()),
        ({TOKEN_ID: ApiTokenRecord(OTHER, revoked=False)}, _api()),
    ):
        err = await _refused(_app(users, tokens), claims)
        assert (err.status_code, err.code) == (401, "api_token_revoked"), tokens


@pytest.mark.anyio
async def test_an_api_token_past_its_rows_end_is_refused() -> None:
    """A token made to last for ever carries no exp claim: the row's expires_at ends it."""
    app = _app({USER: ACTIVE}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False, expired=True)})
    err = await _refused(app, _api())
    assert (err.status_code, err.code) == (401, "api_token_expired")


@pytest.mark.anyio
async def test_an_api_token_of_a_deactivated_user_is_refused() -> None:
    app = _app({USER: UserFlags(is_active=False, is_superuser=False)}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False)})
    err = await _refused(app, _api())
    assert err.code == "user_inactive"


@pytest.mark.anyio
async def test_an_api_token_from_before_revocation_is_refused_everywhere() -> None:
    claims = _api()
    del claims["typ"]
    for app in (_app({USER: ACTIVE}), SimpleNamespace(state=SimpleNamespace())):
        err = await _refused(app, claims)
        assert (err.status_code, err.code) == (401, "api_token_outdated")


@pytest.mark.anyio
async def test_a_sign_in_from_before_sign_ins_expired_is_refused() -> None:
    # Console sign-ins had no expiry and no iat before; they must not live on.
    for claims in ({"sub": USER, "aud": ["fastapi-users:auth"]}, {"sub": USER, "aud": "fastapi-users:auth"}):
        err = await _refused(_app({USER: ACTIVE}), claims)
        assert (err.status_code, err.code) == (401, "sign_in_outdated")


@pytest.mark.anyio
async def test_the_bootstrap_token_is_no_api_token() -> None:
    # The CLI's setup token has a jti and the console's audience (and an hour's exp, checked with the signature).
    claims = {"sub": USER, "aud": "fastapi-users:auth", "tenant_id": "default", "iat": 1, "jti": "ab12"}
    await check_caller(_app({USER: ACTIVE}), claims)


@pytest.mark.anyio
async def test_without_the_backend_database_only_the_claims_are_checked() -> None:
    app = SimpleNamespace(state=SimpleNamespace(agent_directory=None))
    await check_caller(app, _login())
    await check_caller(app, _api())


@pytest.mark.anyio
async def test_a_failed_lookup_is_a_retryable_error_not_a_pass() -> None:
    err = await _refused(_app(fail=True), _login())
    assert (err.status_code, err.code) == (503, "access_check_failed")
