"""Sizing the services: pools per worker, connections for Postgres, one tune step for every command.

Each pool was sized by the worker count as well -- 3 connections per worker,
at least 5, as many again on demand -- so what one service could open grew with
the square of its workers: 24 at 8 cores, 96 at 16, 384 at 32. And deploy had a
copy of the sizing of its own, applied only when it generated a new ``.env``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_cli.core import tune
from faden_cli.core.env_gen import default_env_vars, read_env_file, write_env_file
from faden_cli.core.sysinfo import MIN_MAX_CONNECTIONS, SystemInfo, calculate_tune_profile


def _host(cores: int) -> SystemInfo:
    return SystemInfo(physical_cores=cores, logical_cores=cores * 2, total_ram_bytes=64 * 1024**3)


@pytest.mark.parametrize(("cores", "workers"), [(8, 2), (16, 4), (32, 8), (64, 16)])
def test_what_a_service_can_open_grows_with_its_workers_not_their_square(cores: int, workers: int) -> None:
    tp = calculate_tune_profile("prod", system=_host(cores))
    assert tp.workers["gateway"] == workers
    assert (tp.db_pool_size["gateway"], tp.db_max_overflow["gateway"]) == (5, 5)
    assert tp.db_connections()["gateway"] == workers * 10
    assert (tp.rabbit_pool_size["gateway"], tp.rabbit_channel_pool_size["gateway"]) == (2, 10)


@pytest.mark.parametrize(
    ("cores", "peak", "max_connections"), [(8, 90, 250), (32, 210, 350), (64, 370, 500)],
)
def test_postgres_takes_every_service_at_its_peak(cores: int, peak: int, max_connections: int) -> None:
    """Gateway and backend (workers x 10), backend worker (2 x 10), MCP and skills (15 each), 100 spare."""
    tp = calculate_tune_profile("prod", system=_host(cores))
    assert sum(tp.db_connections().values()) == peak
    assert tp.max_connections == max_connections
    assert tp.to_env_dict()["POSTGRES_MAX_CONNECTIONS"] == str(max_connections)


def test_pii_gets_workers_but_no_database_pool() -> None:
    """Its database layer is unused: it opens no connection."""
    env = calculate_tune_profile("prod", system=_host(16)).to_env_dict()
    assert env["FADEN_PII_WORKERS_COUNT"] == "4"
    assert "FADEN_PII_DB_POOL_SIZE" not in env


def test_dev_stays_light() -> None:
    tp = calculate_tune_profile("dev", system=_host(32))
    assert tp.workers["gateway"] == 2
    assert (tp.db_pool_size["gateway"], tp.db_max_overflow["gateway"]) == (5, 10)
    assert tp.max_connections == MIN_MAX_CONNECTIONS


# ── One tune step: faden tune, deploy, upgrade ──────────────────


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tune, "detect_system", lambda: _host(32))
    path = tmp_path / ".env"
    write_env_file(path, {
        "POSTGRES_PASSWORD": "secret", "HF_CACHE_DIR": "/models", "FADEN_GATEWAY_WORKERS_COUNT": "8",
    })
    return path


def test_tuning_writes_the_sizing_and_keeps_the_rest(env_path: Path) -> None:
    tune.tune_env(env_path, profile="prod")
    env = read_env_file(env_path)
    assert env["FADEN_GATEWAY_DB_POOL_SIZE"] == "5"
    assert env["POSTGRES_MAX_CONNECTIONS"] == "350"
    assert (env["POSTGRES_PASSWORD"], env["HF_CACHE_DIR"]) == ("secret", "/models")


def test_deploy_and_upgrade_tune_with_what_tune_chose_last(env_path: Path) -> None:
    """``faden tune --resource-pct 0.5`` holds through the next deploy, which calls tune_env()."""
    tune.tune_env(env_path, profile="prod", resource_pct=0.5)
    tune.tune_env(env_path)
    env = read_env_file(env_path)
    assert (env["FADEN_TUNE_PROFILE"], env["FADEN_TUNE_RESOURCE_PCT"]) == ("prod", "0.5")
    assert env["FADEN_GATEWAY_WORKERS_COUNT"] == "16"


def test_an_install_never_tuned_is_tuned_for_its_environment(env_path: Path) -> None:
    env = read_env_file(env_path)
    write_env_file(env_path, {**env, "FADEN_BACKEND_ENVIRONMENT": "dev"})
    assert tune.tune_env(env_path).profile == "dev"
    write_env_file(env_path, {**env, "FADEN_BACKEND_ENVIRONMENT": "production"})
    assert tune.tune_env(env_path).profile == "prod"


def test_a_dry_run_writes_nothing(env_path: Path) -> None:
    before = env_path.read_text(encoding="utf-8")
    tune.tune_env(env_path, profile="prod", dry_run=True)
    assert env_path.read_text(encoding="utf-8") == before


def test_a_generated_env_leaves_the_sizing_to_tune() -> None:
    """Deploy's own copy of the sizing is gone: it calls tune_env like faden tune."""
    assert not any(key.endswith(("WORKERS_COUNT", "DB_POOL_SIZE")) for key in default_env_vars())


def test_deploy_upgrade_and_up_call_the_tune_step() -> None:
    src = Path(__file__).parents[1] / "src" / "faden_cli"
    for command in ("deploy", "upgrade", "up"):
        source = (src / "commands" / f"{command}.py").read_text(encoding="utf-8")
        assert "tune_env(env_path)" in source, command
