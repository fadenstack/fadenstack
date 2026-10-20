"""Sizing by the memory as well as the CPU; RabbitMQ lets a long task keep its message.

Sized by CPU alone, the 8-core, 7 GB release VM got two of everything and a
model download took the backend's task worker to 2.3 GB: swap filled, the
kernel killed the worker, and the web backend's supervisor replaced a child
that had stopped answering. Every 30 minutes RabbitMQ also gave the download's
message out again, and a second download of the same 132 GB model started.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from faden_cli.core import tune
from faden_cli.core.env_gen import read_env_file, write_env_file
from faden_cli.core.rmq import CONSUMER_TIMEOUT_MS, write_definitions
from faden_cli.core.sysinfo import (
    MAX_DOWNLOAD_RANGES,
    MIN_DOWNLOAD_RANGES,
    SystemInfo,
    calculate_tune_profile,
)

GB = 1024**3
ALL_MODULES = frozenset({"pii", "mcp", "skills"})
COMPOSE = Path(__file__).parents[2] / "shared" / "docker-compose.yaml"


def _host(cores: int, ram_gb: float) -> SystemInfo:
    return SystemInfo(physical_cores=cores, logical_cores=cores, total_ram_bytes=int(ram_gb * GB))


def test_the_release_vm_fits_with_one_pii_owner() -> None:
    """Removing the unused PII task worker leaves room for the web services."""
    tp = calculate_tune_profile("prod", system=_host(8, 7.2), modules=ALL_MODULES)
    assert tp.cpu_workers == 2
    assert tp.workers == {"backend": 2, "gateway": 2, "pii": 1}
    assert tp.download_ranges >= MIN_DOWNLOAD_RANGES
    assert tp.memory_short_mb == 0
    assert sum(tp.memory_mb().values()) <= tp.memory_budget_mb


def test_the_release_vm_with_more_memory_keeps_two_and_a_download_its_share() -> None:
    """The same VM at 10.9 GB, as it runs now."""
    tp = calculate_tune_profile("prod", system=_host(8, 10.9), modules=ALL_MODULES)
    assert tp.workers == {"backend": 2, "gateway": 2, "pii": 1}
    # A download may hold several hundred MB, not the 2 GB hf-xet took unasked.
    assert MIN_DOWNLOAD_RANGES < tp.download_ranges <= 32
    assert sum(tp.memory_mb().values()) <= tp.memory_budget_mb


def test_a_small_server_gets_fewer_workers_and_is_told_it_is_short() -> None:
    tp = calculate_tune_profile("prod", system=_host(8, 4), modules=ALL_MODULES)
    assert tp.cpu_workers == 2
    assert tp.workers == {"backend": 1, "gateway": 1, "pii": 1}
    assert tp.task_workers == 1
    assert tp.download_ranges == MIN_DOWNLOAD_RANGES
    assert tp.memory_short_mb > 0
    assert tp.to_env_dict()["FADEN_BACKEND_TASK_WORKERS"] == "1"


def test_memory_cuts_what_the_cpu_would_allow() -> None:
    """32 cores and 8 GB: eight workers by the CPU, far more than the memory holds."""
    tp = calculate_tune_profile("prod", system=_host(32, 8), modules=frozenset({"pii"}))
    assert tp.cpu_workers == 8
    assert max(tp.workers.values()) < 8
    assert sum(tp.memory_mb().values()) <= tp.memory_budget_mb


def test_a_large_server_is_sized_by_its_cpu() -> None:
    tp = calculate_tune_profile("prod", system=_host(32, 128), modules=ALL_MODULES)
    assert tp.workers["gateway"] == 8
    assert tp.task_workers == 2
    assert tp.download_ranges == MAX_DOWNLOAD_RANGES


def test_pii_workers_take_memory_only_with_pii_on() -> None:
    off = calculate_tune_profile("prod", system=_host(8, 7.2))
    on = calculate_tune_profile("prod", system=_host(8, 7.2), modules=frozenset({"pii"}))
    assert not any(part.startswith("pii") for part in off.memory_mb())
    assert sum(on.memory_mb().values()) > sum(off.memory_mb().values())


def test_tune_counts_the_modules_the_install_switched_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tune, "detect_system", lambda: _host(8, 4))
    path = tmp_path / ".env"
    write_env_file(path, {"POSTGRES_PASSWORD": "secret", "FADEN_BACKEND_PII_ENABLED": "true"})

    tp = tune.tune_env(path)

    assert tp.modules == frozenset({"pii"})
    env = read_env_file(path)
    assert env["FADEN_BACKEND_TASK_WORKERS"] == str(tp.task_workers)
    assert env["FADEN_BACKEND_DOWNLOAD_RANGES"] == str(tp.download_ranges)
    assert env["POSTGRES_PASSWORD"] == "secret"


def test_the_backend_worker_runs_what_tune_chose() -> None:
    worker = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["faden-backend-worker"]
    command = worker["command"]
    assert command[command.index("--workers") + 1] == "${FADEN_BACKEND_TASK_WORKERS:-2}"
    assert worker["environment"]["HF_XET_NUM_CONCURRENT_RANGE_GETS"] == "${FADEN_BACKEND_DOWNLOAD_RANGES:-16}"


def test_rabbitmq_lets_a_task_hold_its_message_for_as_long_as_a_download(tmp_path: Path) -> None:
    write_definitions(tmp_path, admin_pass="a", backend_pass="b", api_pass="c")
    conf = (tmp_path / "rabbitmq" / "10-faden.conf").read_text(encoding="utf-8")
    assert f"consumer_timeout = {CONSUMER_TIMEOUT_MS}" in conf
    assert CONSUMER_TIMEOUT_MS >= 24 * 3600 * 1000
