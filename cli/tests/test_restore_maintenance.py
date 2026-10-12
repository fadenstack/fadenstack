"""``faden restore`` keeps nginx up behind the maintenance page.

``compose down`` removed nginx with the rest, so the console and the API refused connections until the restore
ended. The restore now stops everything but nginx, and the maintenance flag is down from the first stop to the
last start, however the restore ends.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import maintenance
from faden_cli.core.backup import RestoreResult

SERVICES = ["nginx", "postgres", "faden-backend", "faden-gateway", "minio"]


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, bool]]:
    """Every compose call and restore step as (what, services, whether the maintenance flag was down)."""
    log: list[tuple[str, Any, bool]] = []
    install = tmp_path / "install"
    install.mkdir()
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    ctx = SimpleNamespace(project_dir=install, env_file=install / ".env")

    def note(name: str):
        def inner(_ctx: object, *, services: list[str] | None = None, **_: object) -> int:
            log.append((name, services, maintenance.state(install) is not None))
            return 0
        return inner

    monkeypatch.setattr(restore, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(restore, "build_context_from_config", lambda cfg: ctx)
    monkeypatch.setattr(restore, "read_manifest", lambda path: {"databases": {}, "volumes": []})
    monkeypatch.setattr(restore, "verify_backup", lambda path, manifest: [])
    monkeypatch.setattr(restore, "service_names", lambda ctx: list(SERVICES))
    monkeypatch.setattr(restore, "compose_stop", note("stop"))
    monkeypatch.setattr(restore, "compose_up", note("up"))
    monkeypatch.setattr(restore, "compose_restart", note("restart"))
    return log


def _run(tmp_path: Path) -> Any:
    return CliRunner().invoke(restore.restore_cmd, [str(tmp_path / "backup"), "-y"])


def test_nginx_is_not_stopped_and_the_maintenance_page_is_up_throughout(
    tmp_path: Path, calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_restore(ctx: object, **_: object) -> RestoreResult:
        calls.append(("restore", None, maintenance.state(ctx.project_dir) is not None))  # type: ignore[attr-defined]
        return RestoreResult(databases_restored=["faden_backend"])

    monkeypatch.setattr(restore, "restore_backup", fake_restore)

    result = _run(tmp_path)

    assert result.exit_code == 0, result.output
    stopped = next(services for what, services, _ in calls if what == "stop")
    assert "nginx" not in stopped and set(stopped) == set(SERVICES) - {"nginx"}
    assert [what for what, _, _ in calls] == ["stop", "up", "restore", "up"]
    assert all(down for _, _, down in calls), "the flag was down at every step"
    assert maintenance.state(tmp_path / "install") is None, "and lifted at the end"


def test_the_maintenance_page_is_lifted_when_the_restore_fails(
    tmp_path: Path, calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        restore, "restore_backup", lambda ctx, **_: RestoreResult(ok=False, errors=["Checksum mismatch"])
    )

    result = _run(tmp_path)

    assert result.exit_code == 1
    assert maintenance.state(tmp_path / "install") is None


def test_nginx_starts_again_to_serve_a_restored_certificate(
    tmp_path: Path, calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        restore, "restore_backup", lambda ctx, **_: RestoreResult(tls_restored=["tls/edge.crt"])
    )

    assert _run(tmp_path).exit_code == 0

    assert calls[-1][:2] == ("restart", ["nginx"])
