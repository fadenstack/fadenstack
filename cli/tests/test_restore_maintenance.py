"""``faden restore`` keeps nginx up behind the maintenance page.

``compose down`` removed nginx with the rest, so the console and the API refused connections until the restore
ended. The restore now stops everything but nginx (and Postgres, which renames the databases), and the
maintenance flag is down from the first stop to the last start, however the restore ends. The databases are
restored beside the live ones before that, while the install still serves: no maintenance page then.
These tests fake the database steps; ``test_restore_beside.py`` runs them on a real Postgres.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import maintenance, provenance_keys, restore_swap
from faden_cli.core.backup import Kept, Prepared

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

    def step(name: str, value: object):
        def inner(*_a: object, **_k: object) -> object:
            log.append((name, None, maintenance.state(install) is not None))
            return value
        return inner

    swapped = [restore_swap.Swapped("faden_backend", True)]
    monkeypatch.setattr(restore, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(restore, "build_context_from_config", lambda cfg: ctx)
    monkeypatch.setattr(restore, "read_manifest", lambda path: {"databases": {"faden_backend": {}}, "volumes": []})
    monkeypatch.setattr(restore, "verify_backup", lambda path, manifest: [])
    monkeypatch.setattr(restore, "service_names", lambda ctx: list(SERVICES))
    monkeypatch.setattr(restore, "compose_stop", note("stop"))
    monkeypatch.setattr(restore, "compose_up", note("up"))
    monkeypatch.setattr(restore, "compose_restart", note("restart"))
    monkeypatch.setattr(restore, "prepare_restore", step("beside", Prepared(["faden_backend"], {"faden_backend": 1}, [], [])))
    monkeypatch.setattr(restore_swap, "swap_in", step("swap", swapped))
    monkeypatch.setattr(restore_swap, "drop_old", step("drop old", []))
    monkeypatch.setattr(restore, "keep_before_restore", step("keep", Kept(work_dir=tmp_path / "kept")))
    return log


def _run(tmp_path: Path) -> Any:
    return CliRunner().invoke(restore.restore_cmd, [str(tmp_path / "backup"), "-y"])


def test_nginx_is_not_stopped_and_the_maintenance_page_is_up_from_the_stop_to_the_start(
    tmp_path: Path, calls: list
) -> None:
    result = _run(tmp_path)

    assert result.exit_code == 0, result.output
    stopped = next(services for what, services, _ in calls if what == "stop")
    assert "nginx" not in stopped and set(stopped) == set(SERVICES) - {"nginx", "postgres"}
    assert [what for what, _, _ in calls] == ["up", "beside", "stop", "up", "swap", "keep", "up", "drop old"]
    first_stop = [what for what, _, _ in calls].index("stop")
    assert not any(down for _, _, down in calls[:first_stop]), "the restore beside runs while the install serves"
    assert all(down for _, _, down in calls[first_stop:]), "the flag was down from the stop to the last start"
    assert maintenance.state(tmp_path / "install") is None, "and lifted at the end"


def test_the_maintenance_page_is_lifted_when_the_swap_fails(
    tmp_path: Path, calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_k: object) -> list[restore_swap.Swapped]:
        raise restore_swap.SwapFailed("database is being accessed by other users")

    monkeypatch.setattr(restore_swap, "swap_in", refuse)
    monkeypatch.setattr(restore_swap, "drop", lambda names, docker="docker": [])

    result = _run(tmp_path)

    assert result.exit_code == 1
    assert "nothing was renamed" in " ".join(result.output.split())
    assert maintenance.state(tmp_path / "install") is None


def test_nginx_starts_again_to_serve_a_restored_certificate(
    tmp_path: Path, calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        restore, "read_manifest",
        lambda path: {"databases": {"faden_backend": {}}, "volumes": [], "tls_material": {"tls/edge.crt": "x"}},
    )
    monkeypatch.setattr(restore, "restore_tls", lambda *a, **k: ["tls/edge.crt"])
    monkeypatch.setattr(provenance_keys, "ensure_step", lambda *a, **k: None)

    assert _run(tmp_path).exit_code == 0

    assert [c[:2] for c in calls][-2] == ("restart", ["nginx"]), "after the start, before the old ones are dropped"
