"""The dashboard layout: saved per user, numbered by the server, refused when stale.

Two devices editing one layout are ordered by the revision the server assigns,
not by whose clock says later; a save based on an old revision gets 409 and
the stored layout back.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.models.users import User, current_active_user

pytestmark = pytest.mark.anyio()


async def _seed_user(dbsession: AsyncSession, *, is_superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"dash-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=is_superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


@pytest.fixture
async def superuser(dbsession: AsyncSession) -> User:
    return await _seed_user(dbsession, is_superuser=True)


@pytest.fixture
def authed_app(fastapi_app: FastAPI, superuser: User) -> FastAPI:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    return fastapi_app


def _layout(**overrides: object) -> dict[str, object]:
    layout: dict[str, object] = {
        "version": 2,
        "order": ["kpis", "traffic", "attention"],
        "hidden": ["attention"],
        "sizes": {"traffic": "L"},
        "kpis": ["requests_today", "spend_month"],
        "traffic_window": "today",
        "usage_range": "7d",
        "fleet_color_by": "health",
    }
    layout.update(overrides)
    return layout


async def test_a_new_user_has_no_layout_and_revision_zero(authed_app: FastAPI, client: AsyncClient) -> None:
    resp = await client.get(authed_app.url_path_for("admin_dashboard_layout"))

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json() == {"layout": None, "revision": 0}
    assert resp.headers["cache-control"] == "no-store, private"


async def test_saves_are_numbered_and_read_back(authed_app: FastAPI, client: AsyncClient) -> None:
    url = authed_app.url_path_for("admin_dashboard_layout_save")

    first = await client.put(url, json={"layout": _layout(), "base_revision": 0})
    assert first.status_code == status.HTTP_200_OK, first.text
    assert first.json()["revision"] == 1

    second = await client.put(url, json={"layout": _layout(hidden=[]), "base_revision": 1})
    assert second.status_code == status.HTTP_200_OK, second.text
    assert second.json()["revision"] == 2

    got = (await client.get(authed_app.url_path_for("admin_dashboard_layout"))).json()
    assert got["revision"] == 2
    assert got["layout"]["hidden"] == []
    assert got["layout"]["sizes"] == {"traffic": "L"}


async def test_a_save_based_on_an_old_revision_is_refused_with_the_stored_layout(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    url = authed_app.url_path_for("admin_dashboard_layout_save")
    await client.put(url, json={"layout": _layout(hidden=["kpis"]), "base_revision": 0})

    # A second device, still on revision 0, saves its own edit.
    stale = await client.put(url, json={"layout": _layout(hidden=[]), "base_revision": 0})

    assert stale.status_code == status.HTTP_409_CONFLICT, stale.text
    body = stale.json()
    assert body["revision"] == 1
    assert body["layout"]["hidden"] == ["kpis"]  # the first device's edit stands
    assert stale.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize(
    "bad",
    [
        {"sizes": {"traffic": "XXL"}},
        {"order": ["kpis", "kpis"]},
        {"kpis": ["a", "b", "c", "d", "e", "f", "g"]},
        {"order": ["Not-A-Widget"]},
        {"traffic_window": "1y"},
        {"surprise": True},
    ],
)
async def test_a_malformed_layout_is_rejected(
    authed_app: FastAPI,
    client: AsyncClient,
    bad: dict[str, object],
) -> None:
    resp = await client.put(
        authed_app.url_path_for("admin_dashboard_layout_save"),
        json={"layout": _layout(**bad), "base_revision": 0},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, resp.text


async def test_an_unknown_widget_id_of_the_right_shape_is_kept(
    authed_app: FastAPI, client: AsyncClient
) -> None:
    """A widget added to the frontend needs no backend change to be placed."""
    resp = await client.put(
        authed_app.url_path_for("admin_dashboard_layout_save"),
        json={"layout": _layout(order=["kpis", "brand_new_widget"]), "base_revision": 0},
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["layout"]["order"] == ["kpis", "brand_new_widget"]


async def test_non_superusers_get_403(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession
) -> None:
    operator = await _seed_user(dbsession, is_superuser=False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: operator

    got = await client.get(fastapi_app.url_path_for("admin_dashboard_layout"))
    put = await client.put(
        fastapi_app.url_path_for("admin_dashboard_layout_save"),
        json={"layout": _layout(), "base_revision": 0},
    )

    assert got.status_code == status.HTTP_403_FORBIDDEN
    assert put.status_code == status.HTTP_403_FORBIDDEN


async def test_the_preferences_patch_cannot_write_the_layout(
    authed_app: FastAPI, client: AsyncClient
) -> None:
    patch = authed_app.url_path_for("update_me_preferences")

    refused = await client.patch(patch, json={"preferences": {"dashboard": {"revision": 99}}})
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, refused.text


async def test_other_preferences_leave_the_layout_and_its_revision_alone(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    await client.put(
        authed_app.url_path_for("admin_dashboard_layout_save"),
        json={"layout": _layout(), "base_revision": 0},
    )

    ok = await client.patch(
        authed_app.url_path_for("update_me_preferences"), json={"preferences": {"profile": "team"}}
    )
    assert ok.status_code == status.HTTP_200_OK, ok.text

    got = (await client.get(authed_app.url_path_for("admin_dashboard_layout"))).json()
    assert got["revision"] == 1
    assert got["layout"]["order"] == ["kpis", "traffic", "attention"]
