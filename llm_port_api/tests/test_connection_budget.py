"""The startup check that this service's database pool fits its Postgres.

Pools used to be sized from the CPU count; on a 64-thread workstation one
worker could open 384 connections against Postgres' 100, and under load the
database refused every service at once. Nothing said so at startup.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from llm_port_api.db.connection_budget import assess, check_connection_budget

PREFIX = "LLM_PORT_API_"


def _assess(**changes: Any) -> Any:
    args: dict[str, Any] = {
        "service": "this service",
        "env_prefix": PREFIX,
        "workers": 1,
        "pool_size": 5,
        "max_overflow": 10,
        "max_connections": 100,
        "reserved": 3,
    }
    args.update(changes)
    return assess(**args)


def test_a_pool_postgres_cannot_hold_is_an_error_that_says_what_to_change() -> None:
    """The sizing of 2026-09-25: CPU x 3 kept and on demand, 64 threads."""
    budget = _assess(pool_size=192, max_overflow=192)
    assert budget.level == logging.ERROR
    assert "up to 384" in budget.message
    assert "accepts only 97" in budget.message
    for setting in ("WORKERS_COUNT", "DB_POOL_SIZE", "DB_MAX_OVERFLOW"):
        assert f"{PREFIX}{setting}" in budget.message
    assert "max_connections" in budget.message


def test_workers_multiply_the_pool() -> None:
    """Every uvicorn worker keeps a pool of its own."""
    assert _assess(workers=4, pool_size=10, max_overflow=20).level == logging.ERROR  # 120 > 97


def test_more_than_half_of_postgres_is_a_warning() -> None:
    budget = _assess(workers=4)  # 60 of 97
    assert budget.level == logging.WARNING
    assert "leaving 37" in budget.message


def test_a_pool_that_fits_is_reported_with_its_numbers() -> None:
    budget = _assess()
    assert budget.level == logging.INFO
    assert "up to 15 of 97" in budget.message


def test_a_bigger_postgres_takes_a_bigger_pool() -> None:
    """Scaling is a matter of settings on both sides, and the check follows them."""
    assert _assess(workers=4, max_connections=300).level == logging.INFO


@pytest.mark.anyio
async def test_a_database_that_is_not_postgres_is_not_checked() -> None:
    """Tests run on SQLite; there is no connection limit to read."""
    engine: Any = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    assert await check_connection_budget(
        engine, service="this service", env_prefix=PREFIX, workers=1, pool_size=5, max_overflow=10,
    ) is None
