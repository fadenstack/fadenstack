"""The maintenance page during an upgrade, and by hand.

Before it, every page of an install being upgraded answered nginx's "502 Bad
Gateway" (or nothing at all) for minutes, and a console left open showed its
calls failing one by one.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from faden_cli.commands import upgrade
from faden_cli.core import compose, maintenance
from faden_cli.core.compose import ComposeContext

SHARED = Path(__file__).resolve().parents[2] / "shared"


def test_the_flag_says_why_and_since_when(tmp_path: Path) -> None:
    assert maintenance.state(tmp_path) is None
    maintenance.enter(tmp_path, reason="manual", message="Back at 15:00")

    found = maintenance.state(tmp_path)
    assert found is not None
    assert found["reason"] == "manual" and found["message"] == "Back at 15:00" and found["since"]
    assert json.loads((tmp_path / "nginx" / "maintenance.flag").read_text(encoding="utf-8")) == found

    assert maintenance.leave(tmp_path) is True
    assert maintenance.state(tmp_path) is None
    assert maintenance.leave(tmp_path) is False


def test_an_upgrade_that_fails_does_not_leave_the_platform_hidden(tmp_path: Path) -> None:
    with pytest.raises(SystemExit), maintenance.during(tmp_path, reason="upgrade"):
        assert maintenance.state(tmp_path) == {"reason": "upgrade", "since": maintenance.state(tmp_path)["since"]}
        raise SystemExit(1)
    assert maintenance.state(tmp_path) is None


def test_maintenance_switched_on_by_hand_stays_on(tmp_path: Path) -> None:
    maintenance.enter(tmp_path, reason="manual")
    with maintenance.during(tmp_path, reason="upgrade"):
        pass
    assert maintenance.state(tmp_path)["reason"] == "manual"


def test_nginx_reads_the_flag_the_cli_writes() -> None:
    """The flag's place in the install is where the shipped nginx config looks for it."""
    locations = (SHARED / "nginx" / "locations.conf").read_text(encoding="utf-8")
    assert maintenance.FLAG.parts[0] == "nginx"  # mounted at /etc/nginx/faden
    assert f"/etc/nginx/faden/{maintenance.FLAG.name}" in locations
    assert (SHARED / "nginx" / "maintenance.html").is_file()
    assert maintenance.FLAG.as_posix() in (SHARED / ".gitignore").read_text(encoding="utf-8")


def test_the_front_door_restarts_first_and_alone_behind_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recreated first with the new config, nginx serves the page while the rest restarts -- and only once."""
    ran: list[tuple[list[str], bool]] = []

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        ran.append((cmd, maintenance.state(tmp_path) is not None))
        out = "postgres\nfaden-backend\nfaden-frontend\nnginx\n" if "--services" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(compose, "_run", run)
    monkeypatch.setattr(compose.shutil, "which", lambda name: name)
    monkeypatch.setattr("faden_cli.commands.deploy._sync_postgres_password", lambda *_: None)
    monkeypatch.setattr("faden_cli.commands.deploy.retire_fixed_logins", lambda *_: [])
    monkeypatch.setattr("faden_cli.core.clickhouse.drop_stale_logs", lambda _ctx: [])
    monkeypatch.setattr("faden_cli.core.bootstrap.wait_for_backend", lambda *_a, **_k: True)
    monkeypatch.setattr("faden_cli.core.edge.console_url", lambda *_: "http://127.0.0.1")
    ctx = ComposeContext(compose_files=[tmp_path / "docker-compose.yaml"])

    with maintenance.during(tmp_path, reason="upgrade"):
        upgrade._restart_and_check(ctx, tmp_path, tmp_path / ".env", 4)

    ups = [(cmd[cmd.index("up") + 1 :], flagged) for cmd, flagged in ran if "up" in cmd]
    assert len(ups) == 2 and all(flagged for _, flagged in ups)
    first, rest = ups[0][0], ups[1][0]
    assert "--no-deps" in first and "--force-recreate" in first and first[-1] == "nginx"
    assert "nginx" not in rest and "--wait" in rest and rest[-3:] == ["postgres", "faden-backend", "faden-frontend"]
    assert maintenance.state(tmp_path) is None
