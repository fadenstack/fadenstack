"""GET /api/admin/notices: what core and the registered sources have to say, for superusers (services/extensions.py)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import tenant_restrictions
from faden_backend.services.extensions import AdminNotice, registry

pytestmark = pytest.mark.anyio


def _signed_in(app: FastAPI, *, superuser: bool) -> None:
    user = User(id=uuid.uuid4(), email="admin@example.com", hashed_password="x",  # noqa: S106
                is_active=True, is_verified=True, is_superuser=superuser)
    app.dependency_overrides[current_active_user] = lambda: user


async def _licence(session: Any) -> list[AdminNotice]:
    assert session is not None  # a source gets the request's session
    return [AdminNotice("licence_expiring", "warning", {"days": 12})]


async def _seats(_session: Any) -> list[AdminNotice]:
    return [AdminNotice("seats_over", "info", {"used": 12, "seats": 10}),
            AdminNotice("licence_expired", "error")]


async def _broken(_session: Any) -> list[AdminNotice]:
    msg = "the source is down"
    raise RuntimeError(msg)


async def test_core_has_none(fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "admin_notices", [])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_200_OK
    assert r.json() == []


async def test_every_source_errors_first(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_licence, _seats])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.json() == [
        {"code": "licence_expired", "severity": "error", "params": {}},
        {"code": "licence_expiring", "severity": "warning", "params": {"days": 12}},
        {"code": "seats_over", "severity": "info", "params": {"used": 12, "seats": 10}},
    ]


async def test_a_broken_source_hides_nothing_else(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_broken, _licence])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_200_OK
    assert [n["code"] for n in r.json()] == ["licence_expiring"]
    assert "An admin notice source failed" in caplog.text


async def test_only_superusers_read_them(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_licence])
    _signed_in(fastapi_app, superuser=False)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_403_FORBIDDEN


async def test_nobody_without_signing_in(fastapi_app: FastAPI, client: AsyncClient) -> None:
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_restriction_on_the_tenant_nothing_enforces_is_told_and_can_be_lifted(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extension's mark on the tenant with the extension gone: a notice, and its action."""
    monkeypatch.setattr(registry, "admin_notices", [])
    admin = User(id=uuid.uuid4(), email=f"admin-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
                 is_active=True, is_verified=True, is_superuser=True)
    dbsession.add(admin)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    name = tenant_restrictions.TOOL_MODE_CEILINGS
    await tenant_restrictions.set_restricted(dbsession, name, on=True)
    await tenant_restrictions.set_restricted(dbsession, name, on=True)  # once is enough
    with pytest.raises(ValueError, match="does not know"):
        await tenant_restrictions.set_restricted(dbsession, "something_else", on=True)

    notices = fastapi_app.url_path_for("admin_notices")
    lift = fastapi_app.url_path_for("lift_tenant_restriction", name=name)
    assert (await client.get(notices)).json() == [
        {"code": "restriction_tool_mode_ceilings", "severity": "warning", "params": {"name": name}},
    ]
    monkeypatch.setattr(registry, "tenant_restrictions", {name})  # the extension is back
    assert (await client.get(notices)).json() == []
    assert (await client.delete(lift)).status_code == status.HTTP_409_CONFLICT
    monkeypatch.setattr(registry, "tenant_restrictions", set())

    other = fastapi_app.url_path_for("lift_tenant_restriction", name="other")
    assert (await client.delete(other)).status_code == status.HTTP_404_NOT_FOUND
    _signed_in(fastapi_app, superuser=False)
    assert (await client.delete(lift)).status_code == status.HTTP_403_FORBIDDEN
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    assert (await client.delete(lift)).status_code == status.HTTP_204_NO_CONTENT
    assert await tenant_restrictions.marked(dbsession) == set()
    assert (await client.get(notices)).json() == []
    event = await dbsession.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.restriction.lift"))
    assert event is not None and event.severity == "high" and event.actor_id == admin.id
