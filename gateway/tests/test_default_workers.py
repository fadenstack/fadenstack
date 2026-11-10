"""One uvicorn worker by default on Windows.

Workers there share the supervisor's listening socket, and some connections
are never accepted by any of them: measured on the dev workstation, bursts of
eight requests to a four-worker gateway lost two to seven of them outright,
their server side still owned by the supervisor 60 s later.
"""

from __future__ import annotations

import pytest

from faden_gateway import settings as settings_module


def test_windows_runs_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.sys, "platform", "win32")
    assert settings_module._default_workers() == 1


@pytest.mark.parametrize("cpus, expected", [(1, 1), (2, 2), (4, 4), (8, 8), (64, 8)])
def test_elsewhere_it_follows_the_cpus_up_to_eight(
    monkeypatch: pytest.MonkeyPatch, cpus: int, expected: int
) -> None:
    monkeypatch.setattr(settings_module.sys, "platform", "linux")
    monkeypatch.setattr(settings_module, "_CPU_COUNT", cpus)
    assert settings_module._default_workers() == expected


@pytest.mark.parametrize("workers, overflow, most", [(1, 10, 15), (4, 10, 60), (5, 3, 40), (8, 3, 64)])
def test_more_workers_open_fewer_connections_each_so_the_pools_still_fit(
    workers: int, overflow: int, most: int,
) -> None:
    """Eight workers open at most 64 connections, about what four opened before (60), within Postgres's 100."""
    assert settings_module._default_overflow(workers) == overflow
    assert workers * (5 + overflow) == most
