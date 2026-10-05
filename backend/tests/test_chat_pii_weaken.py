"""The right to weaken a chat's PII policy reaches the gateway only as the backend's signed grant.

The gateway stores it with the chat's override, and the chat's requests keep
it (an admin's tokenize is applied). It used to be the header value ``true``:
anyone who reached the gateway with their own token could send that.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette import status

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.chat import gateway_client
from faden_backend.settings import settings
from faden_backend.web.application import get_app


def _user(*, superuser: bool) -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = superuser
    user.is_verified = True
    return user


def _rbac(*granted: str) -> RbacDAO:
    """Grants ``pii.session`` actions *granted*, nothing else."""
    dao = MagicMock(spec=RbacDAO)

    async def has_permission(_user_id: uuid.UUID, resource: str, action: str) -> bool:
        return resource == "pii.session" and action in granted

    dao.has_permission = AsyncMock(side_effect=has_permission)
    return dao


class _Gateway:
    """The gateway client's PII calls, recording the grant each one sends."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, str | None]] = []

    async def get_pii_defaults(self, jwt: str, *, weaken_grant: str | None = None) -> Any:
        self.grants.append(("defaults", weaken_grant))
        return {}

    async def get_session_pii_policy(self, session_id: str, jwt: str, *, weaken_grant: str | None = None) -> Any:
        self.grants.append(("get", weaken_grant))
        return {}

    async def patch_session_pii_policy(
        self, session_id: str, body: dict[str, Any], jwt: str, *, weaken_grant: str | None = None,
    ) -> Any:
        self.grants.append(("patch", weaken_grant))
        return {}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, _Gateway]:
    application = get_app()
    application.dependency_overrides[get_db_session] = lambda: MagicMock()  # noqa: PLW0108 - a new mock per request
    fake = _Gateway()

    def client() -> _Gateway:
        return fake

    monkeypatch.setattr("faden_backend.web.api.chat.views._client", client)
    return application, fake


async def _calls(application: FastAPI, sid: str) -> None:
    async with AsyncClient(transport=ASGITransport(application), base_url="http://test") as client:
        cookies = {"fapiauth": "fake-jwt-for-test"}
        for r in (
            await client.get("/api/chat/pii-defaults", cookies=cookies),
            await client.get(f"/api/chat/sessions/{sid}/pii-policy", cookies=cookies),
            await client.patch(
                f"/api/chat/sessions/{sid}/pii-policy", json={"egress_mode": "tokenize_reversible"}, cookies=cookies,
            ),
        ):
            assert r.status_code == status.HTTP_200_OK, r.text


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["superuser", "pii.session:weaken"])
async def test_who_may_weaken_sends_a_grant_signed_for_them_and_the_chat(
    who: str, app: tuple[FastAPI, _Gateway],
) -> None:
    application, gateway = app
    user = _user(superuser=who == "superuser")
    application.dependency_overrides[current_active_user] = lambda: user
    application.dependency_overrides[RbacDAO] = lambda: _rbac("read", "strengthen", "weaken")
    sid = str(uuid.uuid4())

    await _calls(application, sid)

    assert [call for call, _ in gateway.grants] == ["defaults", "get", "patch"]
    for call, grant in gateway.grants:
        assert grant, call
        claims = jwt.decode(grant, settings.users_secret, algorithms=["HS256"])
        assert claims["typ"] == gateway_client.PII_WEAKEN_TOKEN_TYPE
        assert claims["sub"] == str(user.id)
        assert claims["sid"] == ("" if call == "defaults" else sid)
        assert 0 < claims["exp"] - time.time() <= gateway_client.PII_WEAKEN_TTL_SEC


@pytest.mark.anyio
async def test_a_user_who_may_only_strengthen_sends_none(app: tuple[FastAPI, _Gateway]) -> None:
    application, gateway = app
    application.dependency_overrides[current_active_user] = lambda: _user(superuser=False)
    application.dependency_overrides[RbacDAO] = lambda: _rbac("read", "strengthen")

    await _calls(application, str(uuid.uuid4()))

    assert gateway.grants == [("defaults", None), ("get", None), ("patch", None)]


@pytest.mark.anyio
async def test_the_grant_goes_in_the_weaken_header() -> None:
    sent: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(dict(request.headers))
        return httpx.Response(200, json={})

    client = gateway_client.GatewayChatClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")  # noqa: SLF001
    try:
        await client.patch_session_pii_policy("s-1", {}, "user-jwt", weaken_grant="signed")
        await client.patch_session_pii_policy("s-1", {}, "user-jwt")
    finally:
        await client.close()
    assert sent[0]["x-pii-allow-weaken"] == "signed"
    assert "x-pii-allow-weaken" not in sent[1]
    assert sent[1]["authorization"] == "Bearer user-jwt"
