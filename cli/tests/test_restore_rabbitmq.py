"""``faden restore`` writes RabbitMQ's users from the .env it restores, and says when services did not start.

RabbitMQ makes its users from ``rabbitmq/definitions.json`` on every start, and the services log in with the
passwords in ``.env``. A restore onto a server rebuilt with ``faden deploy`` (Help: Backups, Rebuild a lost
server) put back the old ``.env`` but kept the file the deploy wrote: RabbitMQ refused the backend and the
gateway (ACCESS_REFUSED), they stayed unhealthy, and the restore still ended with "Restore complete."
(release test bed, 1 November 2026). How the file is written from .env: test_upgrade_rabbitmq.py.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import backend_tool, env_gen, provenance_keys
from faden_cli.core.backup import RestoreResult

RESTORED_ENV = (
    "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_ADMIN_PASS=restored-admin\n"
    "RABBITMQ_BACKEND_PASS=restored-backend\nRABBITMQ_API_PASS=restored-api\n"
)


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An install the restore puts RESTORED_ENV back into; every step it takes, in order, in ``steps``."""
    root = tmp_path / "install"
    root.mkdir()
    (tmp_path / "backup").mkdir()
    ctx = SimpleNamespace(project_dir=root, env_file=root / ".env")
    state = SimpleNamespace(root=root, started=0, steps=[])

    def fake_restore(_ctx: object, **_: object) -> RestoreResult:
        (root / ".env").write_text(RESTORED_ENV, encoding="utf-8")
        return RestoreResult(databases_restored=["faden_backend"], env_restored=True)

    def up(_ctx: object, *, services: list[str] | None = None, **_: object) -> int:
        """Starting postgres works; starting everything returns what the test sets."""
        state.steps.append(("up", services))
        return 0 if services else state.started

    def rabbitmq_users(shared_dir: Path, env_vars: dict[str, str], profiles: list[str]) -> None:
        state.steps.append(("rabbitmq users", shared_dir, env_vars))

    monkeypatch.setattr(restore, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(restore, "build_context_from_config", lambda cfg: ctx)
    monkeypatch.setattr(restore, "read_manifest", lambda path: {"databases": {}, "volumes": []})
    monkeypatch.setattr(restore, "verify_backup", lambda path, manifest: [])
    monkeypatch.setattr(restore, "service_names", lambda ctx: ["nginx", "postgres", "rabbitmq", "backend"])
    monkeypatch.setattr(restore, "compose_stop", lambda *a, **k: 0)
    monkeypatch.setattr(restore, "compose_restart", lambda *a, **k: 0)
    monkeypatch.setattr(restore, "compose_up", up)
    monkeypatch.setattr(restore, "restore_backup", fake_restore)
    monkeypatch.setattr(restore, "_regenerate_rmq_definitions", rabbitmq_users, raising=False)
    monkeypatch.setattr(restore, "_sync_postgres_password", lambda *a, **k: None)
    monkeypatch.setattr(env_gen, "fill_required_env", lambda *a, **k: [])
    monkeypatch.setattr(provenance_keys, "ensure_step", lambda *a, **k: None)
    monkeypatch.setattr(backend_tool, "record_event", lambda *a, **k: True)
    return state


def _run(tmp_path: Path) -> Any:
    return CliRunner().invoke(restore.restore_cmd, [str(tmp_path / "backup"), "-y"])


def test_rabbitmqs_users_are_written_from_the_restored_env_before_the_start(
    tmp_path: Path, install: SimpleNamespace
) -> None:
    result = _run(tmp_path)

    assert result.exit_code == 0, result.output
    written = [step for step in install.steps if step[0] == "rabbitmq users"]
    assert len(written) == 1
    _, shared_dir, env_vars = written[0]
    assert shared_dir == install.root
    assert env_vars["RABBITMQ_API_PASS"] == "restored-api"
    assert env_vars["RABBITMQ_BACKEND_PASS"] == "restored-backend"
    assert env_vars["RABBITMQ_ADMIN_PASS"] == "restored-admin"
    assert install.steps.index(written[0]) < install.steps.index(("up", None)), "before it all starts again"
    assert "Restore complete." in result.output


def test_a_restore_whose_services_do_not_start_says_so(tmp_path: Path, install: SimpleNamespace) -> None:
    install.started = 1

    result = _run(tmp_path)

    assert result.exit_code == 1
    assert "Restore complete." not in result.output
    assert "not every service started" in result.output
    assert "faden status" in result.output
