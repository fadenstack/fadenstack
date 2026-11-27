"""The web chat's settings the gateway follows: which models summarise a long chat (0.7.2 K, layer 5).

An administrator switches summaries on per chat model (**Settings → Chat → Summaries of long chats**: the
backend's system setting ``chat.summary_models``, a list of model names, ``{"value": [...]}``); all are off by
default, until the summary's quality on a model is measured. Each worker reads it at most every 30 seconds, on
the turns that would write one. A worker that cannot read it keeps what it read last; before its first read,
and without a backend database, no model writes a summary (fail closed: the line in place of the left-out
messages is used).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import asyncpg

from faden_gateway.services.gateway import usage_settings

log = logging.getLogger(__name__)

KEY = "chat.summary_models"
TTL_SEC = 30.0
#: At most this many model names are taken from the setting, each at most 256 characters (a model alias).
_MAX_MODELS = 200


def _value(raw: Any) -> Any:
    """A ``value_json`` as the backend stores it: ``{"value": ...}``, maybe still a JSON string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw.get("value") if isinstance(raw, dict) else raw


def models_from(value: Any) -> frozenset[str]:
    """The model names a setting's value switches on: strings only, nothing else counts."""
    if not isinstance(value, list):
        return frozenset()
    return frozenset(v for v in value[:_MAX_MODELS] if isinstance(v, str) and v and len(v) <= 256)


async def read(dsn: str | None = None) -> frozenset[str] | None:
    """The models switched on, read from the backend's database; ``None``: it cannot be read."""
    dsn = dsn or usage_settings.backend_dsn()
    if dsn is None:
        return frozenset()
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            raw = await conn.fetchval(
                "SELECT value_json FROM system_setting_value WHERE key = $1", KEY, timeout=5,
            )
        finally:
            await conn.close()
    except Exception as exc:  # the backend's database is away: keep what was read last
        log.warning("The chat settings could not be read from the backend's database: %s", exc)
        return None
    return models_from(_value(raw))


class _Cache:
    def __init__(self) -> None:
        self.found: frozenset[str] | None = None
        self.until = 0.0
        self.lock = asyncio.Lock()


_cache = _Cache()


def remember(found: frozenset[str] | None, ttl_sec: float = TTL_SEC) -> None:
    """Keep *found* as this worker's setting for *ttl_sec*; ``None`` (not read) keeps the last."""
    if found is not None:
        _cache.found = found
    _cache.until = time.monotonic() + ttl_sec


def forget() -> None:
    """Read again at the next use (tests; a worker that wants the newest)."""
    _cache.found = None
    _cache.until = 0.0


async def summary_models() -> frozenset[str]:
    """The models that write a summary, read again when this worker's copy is older than 30 seconds."""
    if time.monotonic() < _cache.until:
        return _cache.found or frozenset()
    async with _cache.lock:
        if time.monotonic() >= _cache.until:
            remember(await read())
    return _cache.found or frozenset()


async def summary_on(model: str) -> bool:
    """Whether chats on *model* write a summary of what they leave out."""
    return bool(model) and model in await summary_models()
