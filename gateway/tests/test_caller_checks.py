"""Who may use the plain /v1 routes: active users, and API tokens that were not revoked."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest

from faden_gateway.services.gateway.agents import AgentDirectory, AgentLookupError, ApiTokenRecord, UserFlags
from faden_gateway.services.gateway.auth import API_TOKEN_TYPE, caller_context, check_caller
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.settings import settings
from faden_gateway.web.api.v1.devices import authenticate_device

USER = "8a0e6d0e-1d4f-4c55-9a43-0b7a8f1f0a01"
OTHER = "5b2f3c1d-7e8a-4b9c-8d0e-1f2a3b4c5d6e"
TOKEN_ID = "0f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"


def _app(
    users: dict[str, UserFlags] | None = None,
    tokens: dict[str, ApiTokenRecord] | None = None,
    *,
    fail: bool = False,
    logged_out: frozenset[str] = frozenset(),
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

    async def fetch_sign_in_revoked(jti: str) -> bool:
        if fail:
            raise AgentLookupError("database down")
        return jti in logged_out

    directory = AgentDirectory(
        fetch_agent=fetch_agent,
        fetch_user=fetch_user,
        fetch_api_token=fetch_api_token,
        fetch_sign_in_revoked=fetch_sign_in_revoked,
    )
    return SimpleNamespace(state=SimpleNamespace(agent_directory=directory))


def _login(sub: str = USER, jti: str = "c0ffee") -> dict[str, Any]:
    now = int(time.time())
    return {"sub": sub, "aud": ["fastapi-users:auth"], "iat": now, "jti": jti, "exp": now + 3600}


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
async def test_a_sign_in_without_a_jti_is_refused() -> None:
    claims = _login()
    del claims["jti"]
    err = await _refused(_app({USER: ACTIVE}), claims)
    assert (err.status_code, err.code) == (401, "sign_in_outdated")


@pytest.mark.anyio
async def test_a_logged_out_sign_in_is_refused_and_the_next_one_is_not() -> None:
    app = _app({USER: ACTIVE}, logged_out=frozenset({"gone"}))
    err = await _refused(app, _login(jti="gone"))
    assert (err.status_code, err.code) == (401, "sign_in_ended")
    await check_caller(app, _login(jti="another"))


@pytest.mark.anyio
async def test_sign_ins_made_before_the_accounts_were_ended_are_refused() -> None:
    now = int(time.time())
    ended = UserFlags(is_active=True, is_superuser=False, sessions_valid_after=float(now))
    old = {**_login(), "iat": now - 60}
    err = await _refused(_app({USER: ended}), old)
    assert (err.status_code, err.code) == (401, "sign_in_ended")
    # One made since, or in the same second, stands; so does an API token (it has its own row).
    await check_caller(_app({USER: ended}), {**_login(), "iat": now})
    await check_caller(_app({USER: ended}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False)}), {**_api(), "iat": now - 60})


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
async def test_an_api_tokens_tenant_is_the_one_its_row_names() -> None:
    """A token's tenant_id claim came from whoever made it; the row holds the tenant the backend gave it."""
    app = _app({USER: ACTIVE}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False, tenant_id="default")})
    claims = {**_api(), "tenant_id": "zzz-other"}
    assert await check_caller(app, claims) == "default"
    auth = await caller_context(app, claims, "the-token")
    assert (auth.user_id, auth.tenant_id, auth.token) == (USER, "default", "the-token")


@pytest.mark.anyio
async def test_other_tokens_keep_the_tenant_they_were_signed_with() -> None:
    app = _app({USER: ACTIVE})
    claims = {**_login(), "tenant_id": "tenant-a"}
    assert await check_caller(app, claims) is None
    assert (await caller_context(app, claims)).tenant_id == "tenant-a"
    assert (await caller_context(app, _login())).tenant_id == "default"


@pytest.mark.anyio
async def test_a_device_signed_in_with_an_api_token_is_in_the_rows_tenant() -> None:
    app = _app({USER: ACTIVE}, {TOKEN_ID: ApiTokenRecord(USER, revoked=False, tenant_id="default")})
    token = jwt.encode({**_api(), "tenant_id": "zzz-other"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    identity = await authenticate_device(app, token)
    assert (identity.tenant_id, identity.user_id) == ("default", USER)


@pytest.mark.anyio
async def test_a_failed_lookup_is_a_retryable_error_not_a_pass() -> None:
    err = await _refused(_app(fail=True), _login())
    assert (err.status_code, err.code) == (503, "access_check_failed")
