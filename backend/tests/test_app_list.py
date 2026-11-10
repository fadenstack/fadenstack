"""The app list: read with the release feed, believed only when signed; administrators told, people offered."""

from __future__ import annotations

import base64
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.dashboard import attention
from faden_backend.services.system_settings.registry import check_app_ids
from faden_backend.services.updates import apps, check, release_feed
from faden_backend.settings import settings

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
SHA = "c" * 64
GH = "https://github.com/fadenstack/fadenstack-apps/releases/download"


def nextcloud(*releases: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "nextcloud",
        "name": {"en": "Fadenstack for Nextcloud", "de": "Fadenstack für Nextcloud"},
        "page": "https://fadenstack.com/agents/nextcloud/",
        "reports_as": ["fadenstack_agent"],
        "connect": "nextcloud",
        "agents": {"default": "nextcloud-assistant"},
        "series": [{"series": "0.1", "supported_until": "2027-06-30"}],
        "releases": list(releases),
    }


def office() -> dict[str, Any]:
    return {
        "id": "office",
        "name": {"en": "Fadenstack for Microsoft Office"},
        "page": "https://fadenstack.com/agents/office/",
        "reports_as": ["Fadenstack Office"],
        "connect": "office",
        "agents": {"word": "word-assistant"},
        "releases": [rel("0.3.0", date="2026-11-20")],
    }


def rel(version: str, **extra: Any) -> dict[str, Any]:
    return {
        "version": version,
        "channel": "stable",
        "date": "2026-10-01",
        "severity": "normal",
        "summary": {"en": f"Release {version}.", "de": f"Version {version}."},
        "files": [{"platform": "nextcloud", "kind": "tarball", "url": f"{GH}/nextcloud-v{version}/app.tar.gz",
                   "sha256": SHA, "size": 10}],
        **extra,
    }


def publish(
    *app_list: dict[str, Any], generated_at: str = "2026-11-24T10:00:00Z", expires: str = "2026-12-08T10:00:00Z",
) -> tuple[bytes, bytes]:
    index = json.dumps({
        "schema": 1, "product": "fadenstack-apps", "generated_at": generated_at, "expires": expires,
        "apps": list(app_list),
    }).encode()
    signature = json.dumps({"key_id": "apps-test", "signature": base64.b64encode(KEY.sign(index)).decode()}).encode()
    return index, signature


@pytest.fixture(autouse=True)
def install(monkeypatch: pytest.MonkeyPatch) -> None:
    """An install of 0.7.0 that trusts the test key for the app list."""
    monkeypatch.setattr(release_feed, "APPS_TRUSTED_KEYS", {"apps-test": PUBLIC.decode()})
    monkeypatch.setattr(settings, "install_release", "0.7.0")
    monkeypatch.setattr(settings, "updates_check_enabled", True)
    monkeypatch.setattr(settings, "updates_channel", "stable")
    monkeypatch.setattr(settings, "updates_feed_url", "")
    monkeypatch.setattr(settings, "updates_app_news", True)
    monkeypatch.setattr(settings, "updates_apps_offered", "")


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the app list's URL answers (the feed's is not this test's); ``asked`` records each fetch."""
    answer: dict[str, Any] = {
        "apps": publish(nextcloud(rel("0.1.9"), rel("0.1.10", severity="security")), office()),
        "asked": [],
    }

    async def fetch(url: str, *, etag: str | None) -> tuple[bytes, bytes, str | None] | None:
        answer["asked"].append((url, etag))
        if not url.endswith("/apps.json"):
            raise check.httpx.ConnectError("not this test's")
        if isinstance(answer["apps"], Exception):
            raise answer["apps"]
        return (*answer["apps"], '"a1"')

    monkeypatch.setattr(check, "fetch", fetch)
    return answer


def seen(*versions: str, name: str = "fadenstack_agent") -> dict[str, list[apps.Seen]]:
    return {name: [apps.Seen(v, NOW - timedelta(days=1)) for v in versions]}


async def test_the_app_list_is_read_beside_the_feed_and_kept(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    state = await apps.check_now(dbsession, now=NOW)
    assert state.id == apps.APPS_ROW and state.error is None and state.etag == '"a1"'
    assert served["asked"] == [("https://fadenstack.com/updates/v1/apps.json", None)]
    catalogue = apps.stored(state)
    assert catalogue is not None and [a.id for a in catalogue.apps] == ["nextcloud", "office"]


async def test_a_mirror_serves_the_app_list_beside_its_feed(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "updates_feed_url", "https://mirror.plant.local/fadenstack/index.json")
    await apps.check_now(dbsession, now=NOW)
    assert served["asked"][0][0] == "https://mirror.plant.local/fadenstack/apps.json"


async def test_a_list_not_signed_with_the_apps_key_is_not_kept(
    dbsession: AsyncSession, served: dict[str, Any],
) -> None:
    await apps.check_now(dbsession, now=NOW)
    index, _ = publish(nextcloud(rel("0.2.0")), generated_at="2026-11-25T10:00:00Z")
    # Signed under the release feed's key id: not one the app list trusts.
    feed_key = Ed25519PrivateKey.generate()
    served["apps"] = (index, json.dumps({"key_id": "fadenstack-2026-10",
                                         "signature": base64.b64encode(feed_key.sign(index)).decode()}).encode())
    state = await apps.check_now(dbsession, now=NOW + timedelta(days=1))
    assert state.error is not None and "does not know" in state.error
    kept = apps.stored(state)
    assert kept is not None and kept.app("nextcloud").releases[0].version == "0.1.10"  # type: ignore[union-attr]


async def test_an_older_list_does_not_replace_the_kept_one(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    await apps.check_now(dbsession, now=NOW)
    served["apps"] = publish(nextcloud(rel("0.1.0")), generated_at="2026-11-01T10:00:00Z")
    state = await apps.check_now(dbsession, now=NOW + timedelta(days=1))
    assert state.error is not None and "older" in state.error


async def test_a_list_already_expired_is_not_taken_in(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    """A valid but old list, served on and on (a stale mirror, a list held back), is refused once it expires."""
    served["apps"] = publish(office(), generated_at="2026-11-01T10:00:00Z", expires="2026-11-15T10:00:00Z")
    state = await apps.check_now(dbsession, now=NOW)
    assert state.error is not None and "expired on 2026-11-15" in state.error
    assert apps.stored(state) is None


async def test_a_kept_list_that_expires_is_shown_with_a_warning(
    dbsession: AsyncSession, served: dict[str, Any],
) -> None:
    await apps.check_now(dbsession, now=NOW)
    later = datetime(2026, 12, 9, tzinfo=UTC)  # past the list's expiry: not renewed since
    state = await apps.load_state(dbsession)
    view = apps.admin_view(state, {}, gateway_ok=True, lang="en", now=later)
    assert view["check"]["expired"] is True and [a["id"] for a in view["apps"]] == ["nextcloud", "office"]
    facts = _Facts(dbsession, [])
    facts.now = later
    items = {i.code: i for i in await attention.app_releases(facts)}  # type: ignore[arg-type]
    assert (items["app_list_expired"].severity, items["app_list_expired"].params) == ("warning", {"since": "2026-12-08"})
    assert not apps.admin_view(state, {}, gateway_ok=True, lang="en", now=NOW)["check"]["expired"]


async def test_an_upload_is_kept_like_a_fetched_list(dbsession: AsyncSession) -> None:
    await apps.store_upload(dbsession, *publish(office()), now=NOW)
    state = await apps.load_state(dbsession)
    assert state is not None and state.source == "upload" and apps.stored(state) is not None
    with pytest.raises(release_feed.FeedError):
        await apps.store_upload(dbsession, b"{}", b"{}", now=NOW)


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self._rows


class _Gateway:
    def __init__(self, rows: list[dict[str, Any]] | Exception) -> None:
        self.rows = rows
        self.params: dict[str, Any] = {}

    async def execute(self, _sql: Any, params: dict[str, Any]) -> _Rows:
        self.params = params
        if isinstance(self.rows, Exception):
            raise self.rows
        return _Rows(self.rows)


async def test_apps_in_use_come_from_the_gateways_manifests_without_names() -> None:
    gateway = _Gateway([
        {"app_name": "fadenstack_agent", "app_version": "0.1.9", "last_seen_at": NOW},
        {"app_name": "fadenstack_agent", "app_version": "0.1.10", "last_seen_at": NOW},
    ])
    found = await apps.in_use(gateway, NOW)  # type: ignore[arg-type]
    assert [s.version for s in found["fadenstack_agent"]] == ["0.1.9", "0.1.10"]
    assert gateway.params["since"] == NOW - timedelta(days=apps.IN_USE_DAYS)
    assert "user" not in apps.IN_USE_SQL.split("FROM")[0]  # who ran it is not read
    assert await apps.in_use(_Gateway(RuntimeError("down")), NOW) == {}  # type: ignore[arg-type]
    assert await apps.in_use(None, NOW) == {}


async def test_the_admin_view_measures_each_app_against_what_runs_here(
    dbsession: AsyncSession, served: dict[str, Any],
) -> None:
    state = await apps.check_now(dbsession, now=NOW)
    view = apps.admin_view(state, seen("0.1.9"), gateway_ok=True, lang="de", now=NOW)
    nc = next(a for a in view["apps"] if a["id"] == "nextcloud")
    assert nc["name"] == "Fadenstack für Nextcloud"
    assert (nc["latest"]["version"], nc["latest"]["summary"]) == ("0.1.10", "Version 0.1.10.")
    assert (nc["outdated"], nc["severity"], nc["offered"]) == (["0.1.9"], "security", False)
    assert nc["connect_files"] == ["nextcloud-occ"]
    off = next(a for a in view["apps"] if a["id"] == "office")
    assert (off["new"], off["in_use"], off["connect_files"]) == (True, [], ["office-policy"])
    assert view["check"]["error"] is None and view["news"] is True


async def test_people_see_only_the_apps_offered(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = await apps.check_now(dbsession, now=NOW)
    assert apps.people_view(state, origin="https://ai.example.de", lang="en")["apps"] == []
    monkeypatch.setattr(settings, "updates_apps_offered", "office")
    view = apps.people_view(state, origin="https://ai.example.de", lang="en")
    assert view["server"] == "https://ai.example.de"
    assert [a["id"] for a in view["apps"]] == ["office"]
    assert view["apps"][0]["latest"]["version"] == "0.3.0"


async def test_administrators_are_emailed_once_about_an_app_security_release_in_use(
    dbsession: AsyncSession, served: dict[str, Any], mail_on: object,
) -> None:
    state = await apps.check_now(dbsession, now=NOW)
    assert await apps.notify(dbsession, state, {}) == 0  # nobody runs it here: nothing to say
    assert await apps.notify(dbsession, state, seen("0.1.9")) == 1
    assert await apps.notify(dbsession, state, seen("0.1.9")) == 0  # once per release
    await dbsession.flush()
    mails = (
        await dbsession.execute(select(NotificationOutbox).where(NotificationOutbox.kind == "admin_alert"))
    ).scalars().all()
    payload = next(m.payload_json for m in mails if m.payload_json["fingerprint"] == "app-release:nextcloud:0.1.10")
    assert payload["subject"] == "Fadenstack for Nextcloud 0.1.10: security update"
    assert "0.1.9" in payload["details"]


async def test_a_normal_app_release_is_never_emailed(
    dbsession: AsyncSession, served: dict[str, Any], mail_on: object,
) -> None:
    served["apps"] = publish(nextcloud(rel("0.1.9"), rel("0.1.10")))
    state = await apps.check_now(dbsession, now=NOW)
    assert await apps.notify(dbsession, state, seen("0.1.9")) == 0


class _Facts:
    def __init__(self, session: AsyncSession, gateway_rows: list[dict[str, Any]]) -> None:
        self.now = NOW
        self.docker = None
        self._session = session
        self._rows = gateway_rows
        self.has_gateway = True

    async def on_backend(self, query: Any) -> Any:
        return await query(self._session)

    async def on_gateway(self, query: Any) -> Any:
        return await query(_Gateway(self._rows))


async def test_the_dashboard_names_app_security_releases_and_news(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    await apps.check_now(dbsession, now=NOW)
    rows = [{"app_name": "fadenstack_agent", "app_version": "0.1.9", "last_seen_at": NOW}]
    items = {i.code: i for i in await attention.app_releases(_Facts(dbsession, rows))}  # type: ignore[arg-type]
    security = items["app_update_security"]
    assert (security.id, security.severity, security.link) == (
        "app_update_security:nextcloud:0.1.10", "warning", "/admin/updates#apps",
    )
    assert security.params == {"app": "Fadenstack for Nextcloud", "version": "0.1.10", "current": "0.1.9"}
    assert items["app_new"].id == "app_new:office"  # first released 5 days ago, nobody here uses it

    monkeypatch.setattr(settings, "updates_app_news", False)
    items = {i.code: i for i in await attention.app_releases(_Facts(dbsession, rows))}  # type: ignore[arg-type]
    assert set(items) == {"app_update_security"}, "news can be switched off, security cannot"


async def test_a_version_past_its_support_is_named(dbsession: AsyncSession, served: dict[str, Any]) -> None:
    await apps.check_now(dbsession, now=NOW)
    rows = [{"app_name": "fadenstack_agent", "app_version": "0.1.10", "last_seen_at": NOW}]
    facts = _Facts(dbsession, rows)
    facts.now = datetime(2027, 7, 2, tzinfo=UTC)
    items = {i.code: i for i in await attention.app_releases(facts)}  # type: ignore[arg-type]
    assert items["app_unsupported"].params == {"app": "Fadenstack for Nextcloud", "version": "0.1.10"}


def _factory(session: AsyncSession) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    return factory


async def test_an_upgraded_install_reads_the_app_list_without_waiting_a_day(
    dbsession: AsyncSession, served: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    monkeypatch.setattr("faden_backend.services.system_settings.runtime_mapping.refresh_runtime_values", nothing)
    # The feed was read an hour ago, by the release before: not due. The app list never was.
    state = await check._state_for_update(dbsession)  # noqa: SLF001
    state.checked_at = state.succeeded_at = NOW - timedelta(hours=1)
    await dbsession.flush()
    assert await check.run_pass(_factory(dbsession), now=NOW)
    assert await apps.load_state(dbsession) is not None
    assert not await check.run_pass(_factory(dbsession), now=NOW + timedelta(hours=1))


@pytest.mark.parametrize("value", ["", "desktop", "desktop, nextcloud,office"])
def test_offered_apps_are_app_ids(value: str) -> None:
    check_app_ids(value)


@pytest.mark.parametrize("value", ["Desktop", "desktop;rm -rf", "../x", ",".join(f"a{i}" for i in range(21))])
def test_anything_else_is_refused_as_offered_apps(value: str) -> None:
    with pytest.raises(ValueError, match="app ids"):
        check_app_ids(value)


async def _user(dbsession: AsyncSession, *, superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"apps-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def test_an_administrator_offers_an_app_and_people_see_it(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, served: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    monkeypatch.setattr(fastapi_app.state, "llm_graph_trace_session_factory", None, raising=False)
    await apps.check_now(dbsession, now=NOW)

    body = (await client.get(fastapi_app.url_path_for("admin_updates_apps"), params={"lang": "en"})).json()
    assert [a["id"] for a in body["apps"]] == ["nextcloud", "office"]
    assert body["in_use_known"] is False

    url = fastapi_app.url_path_for("admin_updates_app_offered", app_id="office")
    resp = await client.put(url, json={"offered": True})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert next(a for a in resp.json()["apps"] if a["id"] == "office")["offered"] is True
    assert settings.updates_apps_offered == "office"
    missing = fastapi_app.url_path_for("admin_updates_app_offered", app_id="teleport")
    assert (await client.put(missing, json={"offered": True})).status_code == status.HTTP_404_NOT_FOUND

    person = await _user(dbsession, superuser=False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: person
    people = (await client.get(fastapi_app.url_path_for("apps_offered"))).json()
    assert [a["id"] for a in people["apps"]] == ["office"]
    assert people["server"].startswith("http")
    assert (await client.get(fastapi_app.url_path_for("admin_updates_apps"))).status_code == 403
    assert (await client.put(url, json={"offered": False})).status_code == 403


async def test_connection_files_are_filled_in_for_this_server_and_hold_no_secret(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, served: dict[str, Any],
) -> None:
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    await apps.check_now(dbsession, now=NOW)

    resp = await client.get(
        fastapi_app.url_path_for("admin_updates_app_connect", app_id="office", kind="office-policy"),
        headers={"Host": "ai.example.de", "X-Forwarded-Proto": "https"},
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.headers["content-disposition"] == 'attachment; filename="policy.json"'
    assert resp.headers["x-content-type-options"] == "nosniff"
    policy = resp.json()
    assert policy["serverUrl"] == "https://ai.example.de"
    assert policy["agents"]["word"] == "word-assistant" and policy["agents"]["excel"] == "excel-assistant"
    assert set(policy) == {"serverUrl", "agents", "trustedCaCertificate"}

    occ = await client.get(
        fastapi_app.url_path_for("admin_updates_app_connect", app_id="nextcloud", kind="nextcloud-occ"),
        headers={"Host": "ai.example.de", "X-Forwarded-Proto": "https"},
    )
    assert "config:app:set fadenstack_agent server_url --value=https://ai.example.de" in occ.text
    assert "--value=nextcloud-assistant" in occ.text

    wrong = fastapi_app.url_path_for("admin_updates_app_connect", app_id="office", kind="nextcloud-occ")
    assert (await client.get(wrong)).status_code == status.HTTP_404_NOT_FOUND


# -- The app part never undoes the release check beside it (check.check_apps) ----------------------------------

CORE_KEY = Ed25519PrivateKey.generate()
CORE_PUBLIC = base64.b64encode(
    CORE_KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
).decode()
FEED_URL = "https://fadenstack.com/updates/v1/index.json"
APPS_URL = "https://fadenstack.com/updates/v1/apps.json"


def core_feed() -> tuple[bytes, bytes]:
    """A release feed with a security release after this install's 0.7.0: its check queues one mail."""
    index = json.dumps({
        "schema": 1, "product": "fadenstack", "generated_at": "2026-11-24T10:00:00Z",
        "releases": [
            {"version": "0.7.0", "channel": "stable", "severity": "normal", "summary": "s"},
            {"version": "0.7.1", "channel": "stable", "severity": "security", "summary": "s"},
        ],
    }).encode()
    signature = json.dumps({"key_id": "core-test", "signature": base64.b64encode(CORE_KEY.sign(index)).decode()})
    return index, signature.encode()


def signed_apps(data: dict[str, Any]) -> tuple[bytes, bytes]:
    index = json.dumps(data).encode()
    return index, json.dumps({"key_id": "apps-test", "signature": base64.b64encode(KEY.sign(index)).decode()}).encode()


@pytest.fixture
def both(served: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The release feed and the app list, each at its URL; ``asked`` records each fetch."""
    monkeypatch.setattr(release_feed, "TRUSTED_KEYS", {"core-test": CORE_PUBLIC})

    async def fetch(url: str, *, etag: str | None) -> tuple[bytes, bytes, str | None] | None:
        served["asked"].append((url, etag))
        if url == FEED_URL:
            return (*core_feed(), '"c1"')
        return (*served["apps"], '"a1"')

    monkeypatch.setattr(check, "fetch", fetch)
    return served


@pytest.fixture
def one_transaction(dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The pass's session factory over the test's session; its commit stays inside the test's transaction."""

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dbsession, "commit", dbsession.flush)
    monkeypatch.setattr("faden_backend.services.system_settings.runtime_mapping.refresh_runtime_values", nothing)
    return _factory(dbsession)


async def _alerts(dbsession: AsyncSession, fingerprint: str) -> int:
    await dbsession.flush()
    rows = (await dbsession.execute(select(NotificationOutbox))).scalars().all()
    return sum(1 for m in rows if (m.payload_json or {}).get("fingerprint") == fingerprint)


async def test_an_error_in_the_app_part_never_undoes_the_release_check(
    dbsession: AsyncSession, both: dict[str, Any], one_transaction: Any, monkeypatch: pytest.MonkeyPatch,
    mail_on: object,
) -> None:
    read_apps = apps.check_now

    async def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the app part broke")

    monkeypatch.setattr(apps, "check_now", broken)
    assert await check.run_pass(one_transaction, now=NOW)
    feed = await check.load_state(dbsession)
    assert feed is not None and (feed.error, feed.succeeded_at) == (None, NOW)  # the release check is kept
    assert await _alerts(dbsession, "release:0.7.1") == 1  # and its notice, once
    row = await apps.load_state(dbsession)
    assert row is not None and row.index_text is None
    assert row.error == "The app list could not be read (RuntimeError). The server's log has the details."

    # The app list is read again on its own after RETRY_AFTER, as a failed feed would be; the feed is not due, and
    # its notice is not sent again.
    monkeypatch.setattr(apps, "check_now", read_apps)
    assert not await check.run_pass(one_transaction, now=NOW + timedelta(hours=1))
    asked = len(both["asked"])
    assert await check.run_pass(one_transaction, now=NOW + check.RETRY_AFTER)
    assert [url for url, _ in both["asked"][asked:]] == [APPS_URL]
    row = await apps.load_state(dbsession)
    assert row is not None and row.error is None and apps.stored(row) is not None
    assert await _alerts(dbsession, "release:0.7.1") == 1


async def test_a_database_error_in_the_app_part_undoes_that_part_only(
    dbsession: AsyncSession, both: dict[str, Any], one_transaction: Any, monkeypatch: pytest.MonkeyPatch,
    mail_on: object,
) -> None:
    """After the list was kept, a failing statement: the savepoint takes back the list, never the release check."""
    from sqlalchemy import text  # noqa: PLC0415

    async def failing_notify(session: AsyncSession, *_a: Any) -> int:
        await session.execute(text("SELECT 1 / 0"))
        return 0

    monkeypatch.setattr(apps, "notify", failing_notify)
    assert await check.run_pass(one_transaction, now=NOW)
    feed = await check.load_state(dbsession)
    assert feed is not None and feed.error is None and await _alerts(dbsession, "release:0.7.1") == 1
    row = await apps.load_state(dbsession)
    assert row is not None and apps.stored(row) is None  # the list kept inside the failed part is taken back
    assert row.error is not None and row.error.startswith("The app list could not be read (")


async def test_a_signed_app_list_shaped_wrongly_is_the_app_lists_error_only(
    dbsession: AsyncSession, both: dict[str, Any], one_transaction: Any, mail_on: object,
) -> None:
    both["apps"] = signed_apps({
        "schema": 1, "product": "fadenstack-apps", "generated_at": "2026-11-24T10:00:00Z",
        "expires": "2026-12-08T10:00:00Z", "apps": 1,
    })
    assert await check.run_pass(one_transaction, now=NOW)
    feed = await check.load_state(dbsession)
    assert feed is not None and feed.error is None and await _alerts(dbsession, "release:0.7.1") == 1
    row = await apps.load_state(dbsession)
    assert row is not None and row.error == "The app list's apps are not a list."


async def test_check_now_keeps_the_release_check_when_the_app_part_fails(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, both: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch, mail_on: object,
) -> None:
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    monkeypatch.setattr(fastapi_app.state, "llm_graph_trace_session_factory", None, raising=False)

    async def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("the app part broke")

    monkeypatch.setattr(apps, "check_now", broken)
    resp = await client.post(fastapi_app.url_path_for("admin_updates_check"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    feed = await check.load_state(dbsession)
    assert feed is not None and feed.error is None and await _alerts(dbsession, "release:0.7.1") == 1
    row = await apps.load_state(dbsession)
    assert row is not None and row.error is not None and "RuntimeError" in row.error


async def test_one_mail_per_app_security_release_not_per_release(
    dbsession: AsyncSession, served: dict[str, Any], mail_on: object,
) -> None:
    """A normal release after a security one brings no news about security: no second mail; the next one does."""
    state = await apps.check_now(dbsession, now=NOW)
    assert await apps.notify(dbsession, state, seen("0.1.9")) == 1  # 0.1.10, security
    served["apps"] = publish(
        nextcloud(rel("0.1.9"), rel("0.1.10", severity="security"), rel("0.1.11")), generated_at="2026-11-25T10:00:00Z",
    )
    state = await apps.check_now(dbsession, now=NOW + timedelta(hours=1))
    assert state.error is None and await apps.notify(dbsession, state, seen("0.1.9")) == 0
    served["apps"] = publish(
        nextcloud(rel("0.1.9"), rel("0.1.10", severity="security"), rel("0.1.11"), rel("0.1.12", severity="security")),
        generated_at="2026-11-25T11:00:00Z",
    )
    state = await apps.check_now(dbsession, now=NOW + timedelta(hours=2))
    assert await apps.notify(dbsession, state, seen("0.1.9")) == 1
    await dbsession.flush()
    mails = (await dbsession.execute(select(NotificationOutbox))).scalars().all()
    subjects = sorted(m.payload_json["subject"] for m in mails if m.payload_json.get("fingerprint", "").startswith("app-"))
    assert subjects == [
        "Fadenstack for Nextcloud 0.1.10: security update", "Fadenstack for Nextcloud 0.1.12: security update",
    ]


async def test_a_version_reported_in_another_spelling_keeps_its_last_sighting(
    dbsession: AsyncSession, served: dict[str, Any],
) -> None:
    state = await apps.check_now(dbsession, now=NOW)
    # The latest sighting under the other spelling: by the reported text alone it would be lost.
    reported = {"fadenstack_agent": [apps.Seen("0.1.9", NOW - timedelta(days=2)), apps.Seen("v0.1.9", NOW)]}
    view = apps.admin_view(state, reported, gateway_ok=True, lang="en", now=NOW)
    nextcloud = next(a for a in view["apps"] if a["id"] == "nextcloud")
    assert nextcloud["in_use"] == [{"version": "0.1.9", "last_seen": NOW}]


def test_the_commands_quote_what_they_fill_in() -> None:
    catalogue = release_feed.parse_apps(json.loads(publish(nextcloud(rel("0.1.10")))[0]))
    app = catalogue.app("nextcloud")
    assert app is not None
    found = apps.connect_file(app, "nextcloud-occ", origin="https://ai.example.de", own_ca=False)
    assert found is not None and "--value=https://ai.example.de\n" in found.content  # nothing to quote: as it is
    odd = apps.connect_file(app, "nextcloud-occ", origin="https://ai.example.de/x;touch y", own_ca=False)
    assert odd is not None and "--value='https://ai.example.de/x;touch y'\n" in odd.content


async def test_an_uploaded_app_list_that_does_not_verify_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    index, _ = publish(office())
    resp = await client.post(
        fastapi_app.url_path_for("admin_updates_upload_apps"),
        files={"index": ("apps.json", index), "signature": ("apps.json.sig", b'{"key_id":"x","signature":""}')},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "does not know" in resp.json()["detail"]
