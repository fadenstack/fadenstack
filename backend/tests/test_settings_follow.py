"""Every backend process takes up a settings change before its next request or task, whoever saved it.

Found when an administrator switched an option off and a sign-in page served by another uvicorn worker still
showed it: a live setting was applied only in the process that took the save. The database counts every change
to the stored settings (``system_settings_epoch``, moved by triggers); each process reads the count before it
works and loads every setting again when it moved (``services/system_settings/runtime_sync.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqMessage

from faden_backend.services.system_settings import runtime_mapping, runtime_sync
from faden_backend.services.system_settings.crypto import SettingsCrypto
from faden_backend.settings import settings
from faden_backend.tkq import broker


async def _store(db: AsyncSession, key: str, value: object) -> None:
    """A value as another process saves it: straight into the table, no one applies it here."""
    await db.execute(
        text(
            "INSERT INTO system_setting_value (key, value_json, version) VALUES (:k, CAST(:v AS jsonb), 1) "
            "ON CONFLICT (key) DO UPDATE SET value_json = EXCLUDED.value_json",
        ),
        {"k": key, "v": f'{{"value": {_json(value)}}}'},
    )


async def _store_secret(db: AsyncSession, key: str, plain: str) -> None:
    await db.execute(
        text(
            "INSERT INTO system_setting_secret (key, ciphertext, kek_version) VALUES (:k, :c, 'v1') "
            "ON CONFLICT (key) DO UPDATE SET ciphertext = EXCLUDED.ciphertext",
        ),
        {"k": key, "c": SettingsCrypto.from_settings().encrypt(plain)},
    )


def _json(value: object) -> str:
    return json.dumps(value)


@pytest.fixture
def probe(fastapi_app: FastAPI) -> FastAPI:
    """A route that answers with a live setting as the request sees it."""

    async def channel() -> dict[str, Any]:
        return {"channel": settings.updates_channel}

    fastapi_app.add_api_route("/api/settings-probe", channel, methods=["GET"])
    return fastapi_app


# ── Requests ────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_value_saved_by_another_process_reaches_the_next_request(
    follow_settings: Any, probe: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    assert (await client.get("/api/settings-probe")).json()["channel"] == settings.updates_channel
    await _store(dbsession, "updates.channel", "preview-from-elsewhere")

    answer = await client.get("/api/settings-probe")

    assert answer.json()["channel"] == "preview-from-elsewhere"


@pytest.mark.anyio
async def test_a_cleared_value_returns_to_the_start_value(
    follow_settings: Any, probe: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    start = follow_settings.baseline["updates.channel"]
    await _store(dbsession, "updates.channel", "preview-from-elsewhere")
    assert (await client.get("/api/settings-probe")).json()["channel"] == "preview-from-elsewhere"

    await _store(dbsession, "updates.channel", "  ")
    assert (await client.get("/api/settings-probe")).json()["channel"] == start, "blank"

    await _store(dbsession, "updates.channel", "preview-from-elsewhere")
    assert (await client.get("/api/settings-probe")).json()["channel"] == "preview-from-elsewhere"
    await dbsession.execute(text("DELETE FROM system_setting_value WHERE key = 'updates.channel'"))
    assert (await client.get("/api/settings-probe")).json()["channel"] == start, "deleted"


@pytest.mark.anyio
async def test_secrets_are_taken_up_and_a_deleted_one_returns_to_the_start_value(
    follow_settings: Any, probe: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    start = follow_settings.baseline["faden_gateway.mcp_service_token"]
    one, two = "token-one", "token-two"
    await _store_secret(dbsession, "faden_gateway.mcp_service_token", one)
    await client.get("/api/settings-probe")
    assert settings.mcp_service_token == one

    await _store_secret(dbsession, "faden_gateway.mcp_service_token", two)
    await client.get("/api/settings-probe")
    assert settings.mcp_service_token == two, "changed"

    await dbsession.execute(text("DELETE FROM system_setting_secret WHERE key = 'faden_gateway.mcp_service_token'"))
    await client.get("/api/settings-probe")
    assert settings.mcp_service_token == start != two, "deleted"


@pytest.mark.anyio
async def test_a_secret_that_cannot_be_opened_keeps_its_value_and_logs_only_a_count(
    follow_settings: Any, probe: FastAPI, client: AsyncClient, dbsession: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _store_secret(dbsession, "faden_gateway.mcp_service_token", "token-one")
    await client.get("/api/settings-probe")
    assert settings.mcp_service_token == "token-one"

    await dbsession.execute(
        text("UPDATE system_setting_secret SET ciphertext = 'not-a-fernet-token' "
             "WHERE key = 'faden_gateway.mcp_service_token'"),
    )
    with caplog.at_level("ERROR"):
        await client.get("/api/settings-probe")

    assert settings.mcp_service_token == "token-one", "kept as it was"
    assert "1 stored secret(s) could not be opened" in caplog.text
    assert "mcp_service_token" not in caplog.text and "not-a-fernet-token" not in caplog.text, (
        "neither the setting nor its sealed value reaches the log"
    )


@pytest.mark.anyio
async def test_the_health_check_reads_nothing(
    follow_settings: Any, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    await _store(dbsession, "updates.channel", "preview-from-elsewhere")
    await client.get("/api/health")
    assert follow_settings.reloads == 0


# ── Keys extensions register ────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_keys_an_extension_registers_are_followed_too(
    follow_settings: Any, dbsession: AsyncSession,
) -> None:
    own = SimpleNamespace(flag="as-started", token="as-started")  # noqa: S106
    runtime_mapping.register_runtime_value_key("ext.test.flag", "flag", target=own)
    runtime_mapping.register_runtime_secret_key("ext.test.token", "token", target=own)
    try:
        await _store(dbsession, "ext.test.flag", "switched")
        await _store_secret(dbsession, "ext.test.token", "sealed")
        assert await runtime_sync.current() is True
        assert (own.flag, own.token) == ("switched", "sealed")

        await _store(dbsession, "ext.test.flag", "")
        await dbsession.execute(text("DELETE FROM system_setting_secret WHERE key = 'ext.test.token'"))
        assert await runtime_sync.current() is True
        assert (own.flag, own.token) == ("as-started", "as-started")
    finally:
        runtime_mapping.get_runtime_value_key_map().pop("ext.test.flag", None)
        runtime_mapping.get_runtime_secret_key_map().pop("ext.test.token", None)
        runtime_mapping._RUNTIME_TARGETS.pop("ext.test.flag", None)  # noqa: SLF001
        runtime_mapping._RUNTIME_TARGETS.pop("ext.test.token", None)  # noqa: SLF001


@pytest.mark.anyio
async def test_a_key_registered_after_the_last_load_is_loaded_without_a_change(
    follow_settings: Any, dbsession: AsyncSession,
) -> None:
    await _store(dbsession, "ext.test.late", "stored")
    assert await runtime_sync.current() is True  # the store above
    own = SimpleNamespace(late="as-started")
    runtime_mapping.register_runtime_value_key("ext.test.late", "late", target=own)
    try:
        assert await runtime_sync.current() is True
        assert own.late == "stored"
    finally:
        runtime_mapping.get_runtime_value_key_map().pop("ext.test.late", None)
        runtime_mapping._RUNTIME_TARGETS.pop("ext.test.late", None)  # noqa: SLF001


# ── Tasks ───────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_each_task_first_takes_up_the_changes(follow_settings: Any, dbsession: AsyncSession) -> None:
    hooks = [m for m in broker.middlewares if isinstance(m, runtime_sync.FollowSettingsTask)]
    assert len(hooks) == 1, "the broker runs the hook"
    await _store(dbsession, "updates.channel", "preview-for-the-worker")
    message = TaskiqMessage(task_id="t", task_name="any", labels={}, args=[], kwargs={})

    assert await hooks[0].pre_execute(message) is message

    assert settings.updates_channel == "preview-for-the-worker"


# ── Cost and order ──────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_an_unchanged_count_costs_one_read_and_no_reload(
    follow_settings: Any, dbsession: AsyncSession,
) -> None:
    await _store(dbsession, "updates.channel", "preview")
    assert await runtime_sync.current() is True
    reloads = follow_settings.reloads
    conn = await dbsession.connection()
    statements: list[str] = []

    def seen(_c: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    event.listen(conn.sync_connection, "before_cursor_execute", seen)
    try:
        assert await runtime_sync.current() is False
        assert await runtime_sync.current() is False
    finally:
        event.remove(conn.sync_connection, "before_cursor_execute", seen)

    assert follow_settings.reloads == reloads
    assert statements == ["SELECT epoch FROM system_settings_epoch WHERE id = 1"] * 2


@pytest.mark.anyio
async def test_a_change_saved_while_loading_is_loaded_by_the_next_request(
    follow_settings: Any, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = runtime_sync._load  # noqa: SLF001
    raced: list[bool] = []

    async def load_while_someone_saves(conn: Any) -> None:
        if not raced:  # another process saves after the count was read, before the values are
            raced.append(True)
            await _store(dbsession, "updates.channel", "saved-meanwhile")
        await load(conn)

    monkeypatch.setattr(runtime_sync, "_load", load_while_someone_saves)
    await _store(dbsession, "updates.channel", "preview")

    assert await runtime_sync.current() is True
    assert await runtime_sync.current() is True, "the count moved again: loaded once more"
    assert await runtime_sync.current() is False
    assert settings.updates_channel == "saved-meanwhile"


@pytest.mark.anyio
async def test_a_process_not_following_reads_nothing(dbsession: AsyncSession) -> None:
    """Before its startup load (the tests, tools) a process keeps the settings it has."""
    assert runtime_sync.following() is False
    await _store(dbsession, "updates.channel", "preview")
    assert await runtime_sync.current(dbsession) is False
