"""The update check: the release feed is read, kept and believed only when signed; administrators are told."""

from __future__ import annotations

import base64
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.node_control import InfraNode
from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.updates import ReleaseFeedState
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.dashboard import attention
from faden_backend.services.updates import check, release_feed, versions
from faden_backend.settings import settings
from faden_backend.web.api.install import AGENT_VERSION

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def publish(*releases: dict[str, Any], generated_at: str = "2026-10-20T10:00:00Z") -> tuple[bytes, bytes]:
    index = json.dumps({
        "schema": 1,
        "product": "fadenstack",
        "generated_at": generated_at,
        "releases": [{"channel": "stable", "severity": "normal", "summary": "s", **r} for r in releases],
    }).encode()
    signature = json.dumps({"key_id": "test", "signature": base64.b64encode(KEY.sign(index)).decode()}).encode()
    return index, signature


@pytest.fixture(autouse=True)
def install(monkeypatch: pytest.MonkeyPatch) -> None:
    """An install of 0.4.1, made with pipx, that trusts the test key."""
    monkeypatch.setattr(release_feed, "TRUSTED_KEYS", {"test": PUBLIC.decode()})
    monkeypatch.setattr(settings, "install_release", "0.4.1")
    monkeypatch.setattr(settings, "cli_installer", "pipx")
    monkeypatch.setattr(settings, "updates_check_enabled", True)
    monkeypatch.setattr(settings, "updates_channel", "stable")
    monkeypatch.setattr(settings, "updates_feed_url", "")
    monkeypatch.setattr(settings, "updates_proxy_url", "")


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the feed URL answers; ``asked`` records each fetch."""
    answer: dict[str, Any] = {"feed": publish({"version": "0.4.1"}, {"version": "0.4.2", "severity": "security"}),
                              "asked": []}

    async def fetch(url: str, *, etag: str | None) -> tuple[bytes, bytes, str | None] | None:
        answer["asked"].append((url, etag))
        if isinstance(answer["feed"], Exception):
            raise answer["feed"]
        if answer["feed"] is None:
            return None
        return (*answer["feed"], '"v1"')

    monkeypatch.setattr(check, "fetch", fetch)
    return answer


async def test_a_signed_feed_is_kept_and_assessed(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    state = await check.check_now(dbsession, now=NOW)
    assert state.error is None and state.succeeded_at == NOW and state.etag == '"v1"'
    assert served["asked"] == [(release_feed.FEED_URL, None)]
    found = check.assessment(state)
    assert found is not None
    assert (found.current, found.latest.version if found.latest else None, found.severity) == (
        "0.4.1", "0.4.2", "security",
    )


async def test_an_unchanged_feed_is_asked_with_its_etag(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    await check.check_now(dbsession, now=NOW)
    served["feed"] = None  # 304
    state = await check.check_now(dbsession, now=NOW + timedelta(days=1))
    assert served["asked"][-1] == (release_feed.FEED_URL, '"v1"')
    assert state.succeeded_at == NOW + timedelta(days=1)
    assert check.assessment(state) is not None  # the kept feed stands


async def test_a_feed_with_a_wrong_signature_is_not_kept(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    index, _ = publish({"version": "9.9.9", "severity": "critical"})
    served["feed"] = (index, json.dumps({"key_id": "test", "signature": base64.b64encode(b"x" * 64).decode()}).encode())
    state = await check.check_now(dbsession, now=NOW)
    assert state.error is not None and "does not match" in state.error
    assert state.index_text is None
    assert check.assessment(state) is None


async def test_an_older_feed_does_not_replace_the_kept_one(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    await check.check_now(dbsession, now=NOW)
    served["feed"] = publish({"version": "0.4.1"}, generated_at="2026-01-01T00:00:00Z")
    state = await check.check_now(dbsession, now=NOW + timedelta(days=1))
    assert state.error is not None and "older" in state.error
    found = check.assessment(state)
    assert found is not None and found.update_available


async def test_offline_is_said_plainly(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    served["feed"] = httpx.ConnectError("Name or service not known")
    state = await check.check_now(dbsession, now=NOW)
    assert state.error is not None and "Could not reach fadenstack.com" in state.error
    assert check.failing(state, NOW)  # never worked: shown at once


def test_when_the_feed_is_read_again() -> None:
    assert check.due(None, NOW)
    ok = ReleaseFeedState(checked_at=NOW, succeeded_at=NOW, error=None)
    assert not check.due(ok, NOW + timedelta(hours=23))
    assert check.due(ok, NOW + timedelta(hours=24))
    failed = ReleaseFeedState(checked_at=NOW, succeeded_at=NOW - timedelta(days=1), error="offline")
    assert not check.due(failed, NOW + timedelta(hours=5))
    assert check.due(failed, NOW + timedelta(hours=6))
    assert not check.failing(failed, NOW)
    assert check.failing(failed, NOW + timedelta(days=7))


async def test_an_upload_is_kept_like_a_fetched_feed(dbsession: AsyncSession) -> None:
    await check.store_upload(dbsession, *publish({"version": "0.5.0"}), now=NOW)
    state = await check.load_state(dbsession)
    assert state is not None and state.source == "upload"
    found = check.assessment(state)
    assert found is not None and found.latest is not None and found.latest.version == "0.5.0"
    with pytest.raises(release_feed.FeedError):
        await check.store_upload(dbsession, b"{}", b"{}", now=NOW)


async def test_administrators_are_emailed_once_about_a_security_release(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch, mail_on: object,
) -> None:
    state = await check.check_now(dbsession, now=NOW)
    assert await check.notify(dbsession, state)
    assert not await check.notify(dbsession, state)  # once per release
    await dbsession.flush()
    mails = (
        await dbsession.execute(select(NotificationOutbox).where(NotificationOutbox.kind == "admin_alert"))
    ).scalars().all()
    payload = next(m.payload_json for m in mails if m.payload_json["fingerprint"] == "release:0.4.2")
    assert "pipx upgrade fadenstack" in payload["details"]
    assert payload["subject"] == "Fadenstack 0.4.2: security update"


async def test_a_normal_release_is_not_emailed(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    served["feed"] = publish({"version": "0.4.1"}, {"version": "0.4.2"})
    state = await check.check_now(dbsession, now=NOW)
    assert not await check.notify(dbsession, state)


def _factory(session: AsyncSession) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


async def test_the_background_pass_reads_only_when_due_and_switched_on(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    # The test session holds one transaction; its commit is the fixture's to undo.
    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    monkeypatch.setattr("faden_backend.services.system_settings.runtime_mapping.refresh_runtime_values", nothing)
    assert await check.run_pass(_factory(dbsession), now=NOW)
    assert not await check.run_pass(_factory(dbsession), now=NOW + timedelta(hours=1))
    monkeypatch.setattr(settings, "updates_check_enabled", False)
    assert not await check.run_pass(_factory(dbsession), now=NOW + timedelta(days=2))
    assert len(served["asked"]) == 1


class _Docker:
    """The core containers, as Docker lists them."""

    def __init__(self, containers: list[dict[str, Any]], images: dict[str, dict[str, str]]) -> None:
        self._containers = containers
        self._images = images

    async def list_containers(self, all_: bool = True) -> list[dict[str, Any]]:
        return self._containers

    async def inspect_container(self, _id: str) -> dict[str, Any]:
        raise LookupError("not in a container")

    async def inspect_image(self, image_id: str) -> dict[str, Any]:
        return {"Config": {"Labels": self._images.get(image_id, {})}}


def _container(service: str, image: str, image_id: str, state: str = "running") -> dict[str, Any]:
    return {
        "Labels": {"com.docker.compose.service": service, "com.docker.compose.project": "fadenstack"},
        "Image": image,
        "ImageID": image_id,
        "State": state,
    }


async def test_each_core_container_is_measured_against_the_release() -> None:
    docker = _Docker(
        [
            _container("faden-backend", "ghcr.io/fadenstack/backend:0.4.1", "sha256:b"),
            _container("faden-gateway", "ghcr.io/fadenstack/gateway:0.4.0", "sha256:g"),
            _container("faden-frontend", "fadenstack-faden-frontend", "sha256:f"),  # a local build
            _container("faden-pii", "ghcr.io/fadenstack/pii:0.4.1", "sha256:p", state="exited"),
            _container("postgres", "pgvector/pgvector:pg17", "sha256:x"),
        ],
        {"sha256:b": {versions.VERSION_LABEL: "0.4.1", versions.REVISION_LABEL: "57bc1e09deadbeef"}},
    )
    parts = {p.name: p for p in await versions.services(docker, "0.4.1")}
    assert set(parts) == {"faden-backend", "faden-gateway", "faden-frontend", "faden-pii"}
    assert (parts["faden-backend"].status, parts["faden-backend"].detail) == (
        "current", "ghcr.io/fadenstack/backend:0.4.1 (57bc1e09dead)",
    )
    assert (parts["faden-gateway"].running, parts["faden-gateway"].status) == ("0.4.0", "mismatch")
    assert parts["faden-frontend"].status == "unknown"
    assert parts["faden-pii"].status == "stopped"


async def test_the_pii_image_variant_is_read_as_its_release() -> None:
    """``0.5.1-cpu`` (tag and label) is release 0.5.1: the Updates page said "unknown" for it."""
    docker = _Docker(
        [
            _container("faden-pii", "ghcr.io/fadenstack/pii:0.4.1-cpu", "sha256:p"),
            _container("faden-pii-worker", "registry.local/pii:0.4.0-gpu", "sha256:w"),
            _container("faden-backend", "ghcr.io/fadenstack/backend:0.4.1-rocm", "sha256:b"),
        ],
        {"sha256:p": {versions.VERSION_LABEL: "0.4.1-cpu"}},
    )
    parts = {p.name: p for p in await versions.services(docker, "0.4.1")}
    assert (parts["faden-pii"].running, parts["faden-pii"].status) == ("0.4.1", "current")
    assert (parts["faden-pii-worker"].running, parts["faden-pii-worker"].status) == ("0.4.0", "mismatch")
    assert parts["faden-backend"].status == "unknown", "only the image variants' suffix is taken off"


class _Facts:
    def __init__(self, session: AsyncSession, docker: Any = None) -> None:
        self.now = NOW
        self.docker = docker
        self._session = session

    async def on_backend(self, query: Any) -> Any:
        return await query(self._session)


async def test_the_dashboard_names_the_release_and_what_lags(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    await check.check_now(dbsession, now=NOW)
    dbsession.add(InfraNode(agent_id=f"a-{uuid.uuid4().hex[:6]}", host="10.0.0.5", version="0.0.1"))
    await dbsession.flush()
    docker = _Docker([_container("faden-gateway", "ghcr.io/fadenstack/gateway:0.4.0", "sha256:g")], {})
    items = {i.code: i for i in await attention.releases(_Facts(dbsession, docker))}  # type: ignore[arg-type]
    update = items["update_available_security"]
    assert (update.id, update.severity, update.params) == (
        "update_available:0.4.2", "warning", {"version": "0.4.2", "current": "0.4.1"},
    )
    assert items["release_mismatch"].params["names"] == "faden-gateway"
    assert items["agent_update_available"].params["version"] == AGENT_VERSION
    assert items["agent_update_available"].params["count"] >= 1


async def _admin(dbsession: AsyncSession, *, superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"updates-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def test_the_updates_page_shows_the_commands(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, served: dict[str, Any]
) -> None:
    admin = await _admin(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.state.docker = None

    resp = await client.post(fastapi_app.url_path_for("admin_updates_check"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["latest"] == "0.4.2" and body["severity"] == "security"
    assert body["commands"] == ["pipx upgrade fadenstack", "faden upgrade --dry-run", "faden upgrade -y"]
    assert body["check"]["error"] is None
    assert (body["current"]["release"], body["current"]["installer"]) == ("0.4.1", "pipx")
    assert body["current"] == {"release": "0.4.1", "backend": body["current"]["backend"], "installer": "pipx"}
    assert any(p["kind"] == "service" and p["name"] == "faden-backend" for p in body["parts"])

    notice = (await client.get(fastapi_app.url_path_for("admin_updates_notice"))).json()
    assert (notice["latest"], notice["severity"], notice["update_available"]) == ("0.4.2", "security", True)


async def test_only_administrators_see_it(fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _admin(dbsession, superuser=False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    for route in ("admin_updates_overview", "admin_updates_notice"):
        assert (await client.get(fastapi_app.url_path_for(route))).status_code == status.HTTP_403_FORBIDDEN
    assert (await client.post(fastapi_app.url_path_for("admin_updates_check"))).status_code == 403


async def test_an_uploaded_feed_that_does_not_verify_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession
) -> None:
    admin = await _admin(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    index, _ = publish({"version": "0.5.0"})
    resp = await client.post(
        fastapi_app.url_path_for("admin_updates_upload_feed"),
        files={"index": ("index.json", index), "signature": ("index.json.sig", b'{"key_id":"x","signature":""}')},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "does not know" in resp.json()["detail"]


async def test_the_page_names_a_newer_agent_only(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, served: dict[str, Any]
) -> None:
    admin = await _admin(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.state.docker = None
    major, minor, patch = (int(x) for x in AGENT_VERSION.split("."))
    for agent, named in ((AGENT_VERSION, None), (f"{major}.{minor}.{patch + 1}", f"{major}.{minor}.{patch + 1}")):
        served["feed"] = publish(
            {"version": "0.4.2", "components": {"agent": agent}},
            generated_at=f"2026-10-2{1 if named else 0}T10:00:00Z",
        )
        body = (await client.post(fastapi_app.url_path_for("admin_updates_check"))).json()
        assert body["released_agent"] == named
