"""`faden up` returns once the services are healthy, as `faden upgrade` and `faden restore` do.

A module just switched on is not ready the moment its container starts: PII loads its model first (about half a
minute on a CPU), and a chat sent in that time was refused ("PII service unavailable"; plan 0.7.0, issue 19).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import up


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`faden up` with compose recorded, not run; ``rc`` is what compose answers, ``rows`` what `ps` lists."""
    seen: dict[str, Any] = {"rc": 0, "rows": []}

    def compose_up(ctx: object, **kwargs: Any) -> int:
        seen["kwargs"] = kwargs
        return seen["rc"]

    monkeypatch.setattr(up, "load_config", lambda: SimpleNamespace(install_dir="/srv/faden", profiles=[]))
    monkeypatch.setattr(up, "build_context_from_config", lambda cfg: SimpleNamespace(env_file=None, project_dir=None))
    monkeypatch.setattr(up, "compose_up", compose_up)
    monkeypatch.setattr(up, "compose_ps", lambda ctx: seen["rows"])
    monkeypatch.setattr(up, "report_inactive_modules", lambda ctx: None)
    monkeypatch.setattr("faden_cli.core.backup.keep_company_files", lambda env_file, ctx: None)
    return seen


def test_it_waits_for_the_services_to_be_healthy(started: dict[str, Any]) -> None:
    result = CliRunner().invoke(up.up_cmd, [])
    assert result.exit_code == 0, result.output
    assert (started["kwargs"]["wait"], started["kwargs"]["timeout"]) == (True, up.WAIT_SECONDS)
    assert "started and healthy" in result.output


@pytest.mark.parametrize("args", [["--no-wait"], ["--no-detach"]])
def test_it_returns_at_once_when_asked_or_in_the_foreground(started: dict[str, Any], args: list[str]) -> None:
    result = CliRunner().invoke(up.up_cmd, args)
    assert result.exit_code == 0, result.output
    assert (started["kwargs"]["wait"], started["kwargs"]["timeout"]) == (False, 0)


def test_the_wait_can_be_longer(started: dict[str, Any]) -> None:
    CliRunner().invoke(up.up_cmd, ["--wait-timeout", "600"])
    assert started["kwargs"]["timeout"] == 600


def test_pull_asks_compose_to_pull(started: dict[str, Any]) -> None:
    """It passed --pull=True, which compose does not take."""
    CliRunner().invoke(up.up_cmd, ["--pull"])
    assert started["kwargs"]["pull"] == "always"
    CliRunner().invoke(up.up_cmd, [])
    assert started["kwargs"]["pull"] == ""


def test_services_not_healthy_in_time_are_named(started: dict[str, Any]) -> None:
    started["rc"] = 1
    started["rows"] = [
        SimpleNamespace(service="faden-pii", name="faden-pii", state="running", status="running", health="starting",
                        exit_code=0),
        SimpleNamespace(service="faden-backend", name="faden-backend", state="running", status="running",
                        health="healthy", exit_code=0),
        SimpleNamespace(service="faden-migrator", name="faden-migrator", state="exited", status="exited", health="",
                        exit_code=0),
    ]
    result = CliRunner().invoke(up.up_cmd, [])
    assert result.exit_code == 1
    assert "faden-pii (starting)" in result.output
    assert "faden-backend" not in result.output and "faden-migrator" not in result.output
