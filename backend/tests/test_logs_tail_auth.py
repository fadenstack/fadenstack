# ruff: noqa: SLF001, S106
"""The live log stream: who may open it, from where, and for how long."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.users import RevokedSignIn, User, end_sign_ins
from faden_backend.settings import settings
from faden_backend.web.api.logs import views as logs_views


class _Session:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Socket:
    """Just enough of a WebSocket for the handshake checks."""

    def __init__(self, dbsession: AsyncSession, token: str | None, headers: dict[str, str] | None = None) -> None:
        self.cookies = {"fapiauth": token} if token else {}
        self.headers = {"host": "console.example", **(headers or {})}
        self.app = SimpleNamespace(state=SimpleNamespace(db_session_factory=lambda: _Session(dbsession)))
        self.closed: tuple[int, str] | None = None
        self.accepted = False

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def accept(self) -> None:
        self.accepted = True


def _token(user: User, jti: str | None = None) -> str:
    return jwt.encode(
        {"sub": str(user.id), "aud": ["fastapi-users:auth"], "iat": 1_700_000_000, "jti": jti or uuid.uuid4().hex},
        settings.users_secret,
        algorithm="HS256",
    )


async def _user(dbsession: AsyncSession, role: str | None, *, superuser: bool = False, active: bool = True) -> User:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    user = User(
        email=f"tail-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=active,
        is_superuser=superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    if role:
        await rbac.assign_role(user.id, (await rbac.get_role_by_name(role)).id)
    return user


@pytest.mark.anyio
async def test_user_without_logs_read_is_refused(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, None)
    ws = _Socket(dbsession, _token(user))
    assert await logs_views._ws_authenticate(ws) is None  # type: ignore[arg-type]
    assert ws.closed is not None
    assert ws.closed[0] == 4403


@pytest.mark.anyio
async def test_user_with_logs_read_is_accepted(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, "admin")
    ws = _Socket(dbsession, _token(user))
    got = await logs_views._ws_authenticate(ws)  # type: ignore[arg-type]
    assert got is not None
    assert got.id == user.id
    assert ws.closed is None


@pytest.mark.anyio
async def test_a_logged_out_or_ended_sign_in_is_refused(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, "admin")
    dbsession.add(RevokedSignIn(jti="logged-out", user_id=user.id, expires_at=None))
    await dbsession.flush()
    ws = _Socket(dbsession, _token(user, "logged-out"))
    assert await logs_views._ws_authenticate(ws) is None  # type: ignore[arg-type]
    assert ws.closed is not None and ws.closed[0] == 4401

    assert await logs_views._ws_authenticate(_Socket(dbsession, _token(user))) is not None  # type: ignore[arg-type]
    end_sign_ins(user)  # the sign-in's iat (2023) is before now
    ws = _Socket(dbsession, _token(user))
    assert await logs_views._ws_authenticate(ws) is None  # type: ignore[arg-type]
    assert ws.closed is not None and ws.closed[0] == 4401


@pytest.mark.anyio
async def test_superuser_passes_without_a_role(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, None, superuser=True)
    assert await logs_views._ws_authenticate(_Socket(dbsession, _token(user))) is not None  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_inactive_user_and_missing_cookie_are_refused(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, "admin", active=False)
    ws = _Socket(dbsession, _token(user))
    assert await logs_views._ws_authenticate(ws) is None  # type: ignore[arg-type]
    assert ws.closed is not None
    assert ws.closed[0] == 4403
    nobody = _Socket(dbsession, None)
    assert await logs_views._ws_authenticate(nobody) is None  # type: ignore[arg-type]
    assert nobody.closed is not None
    assert nobody.closed[0] == 4401


@pytest.mark.anyio
async def test_foreign_origin_is_refused(dbsession: AsyncSession) -> None:
    user = await _user(dbsession, "admin")
    ws = _Socket(dbsession, _token(user), {"origin": "https://evil.example"})
    assert await logs_views._ws_authenticate(ws) is None  # type: ignore[arg-type]
    assert ws.closed is not None
    assert ws.closed[0] == 4403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://console.example"},
        {"origin": "http://localhost:5173", "host": "127.0.0.1:8000", "x-forwarded-host": "localhost:5173"},
        {},  # no Origin: not a browser page
    ],
)
async def test_own_origin_is_accepted(dbsession: AsyncSession, headers: dict[str, str]) -> None:
    user = await _user(dbsession, "admin")
    ws = _Socket(dbsession, _token(user), headers)
    assert await logs_views._ws_authenticate(ws) is not None  # type: ignore[arg-type]


class _Upstream:
    closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_open_stream_is_closed_when_the_permission_goes(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(dbsession, "admin")
    ws = _Socket(dbsession, _token(user))
    upstream = _Upstream()
    monkeypatch.setattr(logs_views, "RECHECK_SEC", 0.01)

    watch = asyncio.create_task(logs_views._ws_recheck(ws, _token(user), upstream))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert not watch.done()  # still allowed: stays open
    assert ws.closed is None

    # The role is taken away.
    rbac = RbacDAO(dbsession)
    await rbac.remove_role(user.id, (await rbac.get_role_by_name("admin")).id)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(watch, timeout=2)
    assert watch.done()
    assert ws.closed is not None
    assert ws.closed[0] == 4403
    assert upstream.closed

