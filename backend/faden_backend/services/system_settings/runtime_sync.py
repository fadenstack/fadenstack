"""Every backend process works with the system settings as they are now, not as it last loaded them.

The settings an administrator changes live (and the extensions' own, registered with
``register_runtime_value_key`` / ``register_runtime_secret_key``) are held in each process's ``settings``
object. Saving one sets it in the process that took the save; every other uvicorn worker and the taskiq worker
kept the old value until restarted: a sign-in page served by another worker still showed what an administrator
had just switched off.

So the database counts the changes (``system_settings_epoch``, moved by triggers in the change's own
transaction; ``db/settings_epoch.py``), and each process reads the count before it works: before each
request (:class:`FollowSettings`), before each task (:class:`FollowSettingsTask`), at each background pass
and wherever ``refresh_runtime_values`` is called. Unchanged, that is one primary-key read. Moved, every
registered key is loaded again, values and secrets, under a lock: a stored value is applied; a missing row
or a blank value puts back the value the process had before it first loaded the settings from the database
(its environment's or the code's default), as a process starting now would have it. The count is read
before the values, so a change saved meanwhile moves it again and the next request loads once more.

A process follows the database once its startup has loaded the settings (:func:`start`); before that (the
tests, tools) nothing here reads anything.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from taskiq import TaskiqMessage, TaskiqMiddleware

from faden_backend.db import settings_epoch
from faden_backend.services.system_settings.runtime_mapping import (
    get_runtime_secret_key_map,
    get_runtime_value_key_map,
    registry_generation,
    runtime_target,
)

log = logging.getLogger(__name__)

_MISSING = object()


class _Follower:
    """What this process loaded, and from where it reads."""

    def __init__(self) -> None:
        self.bind: AsyncEngine | AsyncConnection | None = None
        self.loaded: int | None = None
        self.generation: int | None = None
        #: Each key's value from before the settings were first loaded from the database.
        self.baseline: dict[str, Any] = {}
        self.lock = asyncio.Lock()
        #: How many times the settings were loaded again (for the tests).
        self.reloads = 0


_follower = _Follower()


def following() -> bool:
    """Whether this process follows the settings in the database."""
    return _follower.bind is not None


def capture_baseline() -> None:
    """Note the value of each registered key not noted yet: what a missing or blank stored value restores.

    Called before the startup load, and again before each reload for keys registered since (their value then
    is still the environment's or the code's).
    """
    keys = {**get_runtime_value_key_map(), **get_runtime_secret_key_map()}
    for key, attr in keys.items():
        if key not in _follower.baseline:
            _follower.baseline[key] = getattr(runtime_target(key), attr, _MISSING)


async def start(bind: AsyncEngine | AsyncConnection, load: Callable[[], Awaitable[None]]) -> None:
    """Follow the settings in the database from here on; *load* is the startup load.

    The values from before it are noted first, and the count is read before it, so a change saved while it
    runs is loaded again by the first request.
    """
    capture_baseline()
    generation = registry_generation()
    epoch: int | None = None
    try:
        async with _reading(bind) as conn:
            epoch = await _epoch(conn)
    except Exception:  # the first request loads everything then
        log.warning("Could not read the count of settings changes at startup", exc_info=True)
    await load()
    _follower.bind = bind
    _follower.loaded = epoch
    _follower.generation = generation


def stop() -> None:
    """Stop following (shutdown)."""
    _follower.bind = None


async def current(session: AsyncSession | AsyncConnection | None = None) -> bool:
    """Bring this process's settings up to date with the database; True when they were loaded again.

    Reads through *session* when given (a caller that holds one already, so no second connection is taken),
    else through a connection of its own. Never fails the caller: when the count cannot be read, the settings
    stay as they are and the next call tries again.
    """
    if _follower.bind is None:
        return False
    try:
        if session is not None:
            conn = await session.connection() if isinstance(session, AsyncSession) else session
            return await _sync(conn)
        async with _reading(_follower.bind) as conn:
            return await _sync(conn)
    except Exception as exc:  # a request goes on with the settings it has
        log.warning("Could not check the system settings for changes: %s", exc)
        return False


async def _sync(conn: AsyncConnection) -> bool:
    """Load again when the count or the registered keys moved since the last load.

    The caller holds *conn* before the lock is taken: whoever holds the lock never waits for a connection, so
    requests waiting on it cannot use up the pool it would need.
    """
    if await _epoch(conn) == _follower.loaded and registry_generation() == _follower.generation:
        return False
    async with _follower.lock:
        # Read again: another request may have loaded meanwhile; and before the values, not after.
        epoch = await _epoch(conn)
        generation = registry_generation()
        if epoch == _follower.loaded and generation == _follower.generation:
            return False
        await _load(conn)
        _follower.loaded = epoch
        _follower.generation = generation
        _follower.reloads += 1
        return True


async def _epoch(conn: AsyncConnection) -> int | None:
    value = (await conn.execute(text(settings_epoch.SQL))).scalar_one_or_none()
    return None if value is None else int(value)


@asynccontextmanager
async def _reading(bind: AsyncEngine | AsyncConnection) -> AsyncIterator[AsyncConnection]:
    """A connection to read the count and the settings with.

    From the engine, one in autocommit: each statement is one round trip, with no BEGIN or ROLLBACK around it.
    A connection given instead (the tests' own, inside their transaction) is used as it is.
    """
    if isinstance(bind, AsyncConnection):
        yield bind
        return
    async with bind.connect() as conn:
        yield await conn.execution_options(isolation_level="AUTOCOMMIT")


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _set(key: str, attr: str, value: object) -> None:
    if value is _MISSING:
        return
    object.__setattr__(runtime_target(key), attr, value)


async def _load(conn: AsyncConnection) -> None:
    """Every registered key as stored now: values, then secrets (opened with the settings' master key)."""
    from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from faden_backend.settings import settings  # noqa: PLC0415

    capture_baseline()
    baseline = _follower.baseline
    value_keys = dict(get_runtime_value_key_map())
    secret_keys = dict(get_runtime_secret_key_map())

    rows = await conn.execute(
        text("SELECT key, value_json FROM system_setting_value WHERE key = ANY(:keys)"),
        {"keys": list(value_keys)},
    )
    stored: dict[str, object] = {}
    for row in rows.mappings():
        value = row["value_json"]
        stored[str(row["key"])] = value.get("value", value) if isinstance(value, dict) else value
    for key, attr in value_keys.items():
        value = stored.get(key)
        _set(key, attr, baseline.get(key, _MISSING) if _blank(value) else value)

    if not settings.settings_master_key:
        return  # as at startup: secrets cannot be opened, so they stay as they are
    crypto = SettingsCrypto.from_settings()
    rows = await conn.execute(
        text("SELECT key, ciphertext FROM system_setting_secret WHERE key = ANY(:keys)"),
        {"keys": list(secret_keys)},
    )
    sealed = {str(row["key"]): str(row["ciphertext"]) for row in rows.mappings()}
    for key, attr in secret_keys.items():
        if key not in sealed:
            _set(key, attr, baseline.get(key, _MISSING))
            continue
        try:
            plain = crypto.decrypt(sealed[key])
        except Exception:  # one unreadable secret does not keep the others from loading
            log.exception("Could not open the stored secret '%s'; it stays as it was", key)
            continue
        _set(key, attr, baseline.get(key, _MISSING) if _blank(plain) else plain)


#: Paths that need no settings: the health check, the API description, static files, metrics.
_SKIPPED = ("/api/health", "/api/openapi.json", "/static/", "/metrics")


class FollowSettings:
    """Before each request, take up the settings changes saved since (in any process)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Check the count, load again when it moved, then handle the request."""
        if (
            _follower.bind is not None
            and scope.get("type") in {"http", "websocket"}
            and not str(scope.get("path", "")).startswith(_SKIPPED)
        ):
            await current()
        await self.app(scope, receive, send)


class FollowSettingsTask(TaskiqMiddleware):
    """Before each task, take up the settings changes saved since (in any process)."""

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Check the count, load again when it moved, then run the task."""
        await current()
        return message
