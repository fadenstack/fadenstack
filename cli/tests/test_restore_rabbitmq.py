"""``faden restore`` writes RabbitMQ's users from the .env it restores, and goes back when services do not start.

RabbitMQ makes its users from ``rabbitmq/definitions.json`` on every start, and the services log in with the
passwords in ``.env``. A restore onto a server rebuilt with ``faden deploy`` (Help: Backups, Rebuild a lost
server) put back the old ``.env`` but kept the file the deploy wrote: RabbitMQ refused the backend and the
gateway (ACCESS_REFUSED), they stayed unhealthy, and the restore still ended with "Restore complete."
(release test bed, 1 November 2026). How the file is written from .env: test_upgrade_rabbitmq.py.

A restore whose services do not come back healthy now puts everything back, the .env and RabbitMQ's users
with it, and exits 1 (test_restore_beside.py runs that on a real Postgres). The database steps are faked here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import backend_tool, env_gen, provenance_keys, restore_swap
from faden_cli.core.backup import Kept, Prepared

RESTORED_ENV = (
    "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_ADMIN_PASS=restored-admin\n"
    "RABBITMQ_BACKEND_PASS=restored-backend\nRABBITMQ_API_PASS=restored-api\n"
)
LIVE_ENV = "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_API_PASS=live-api\n"


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An install the restore puts RESTORED_ENV back into; every step it takes, in order, in ``steps``."""
    root = tmp_path / "install"
    root.mkdir()
    (root / ".env").write_text(LIVE_ENV, encoding="utf-8")
    (tmp_path / "backup").mkdir()
    ctx = SimpleNamespace(project_dir=root, env_file=root / ".env")
    state = SimpleNamespace(root=root, started=0, steps=[])

    def fake_restore_env(_backup: Path, env_path: Path) -> bool:
        env_path.write_text(RESTORED_ENV, encoding="utf-8")
        return True

    def up(_ctx: object, *, services: list[str] | None = None, **_: object) -> int:
        """Starting postgres works; starting everything returns what the test sets (the first time)."""
        state.steps.append(("up", services))
        if services:
            return 0
        started, state.started = state.started, 0
        return started

    def rabbitmq_users(shared_dir: Path, env_vars: dict[str, str], profiles: list[str]) -> None:
        state.steps.append(("rabbitmq users", shared_dir, env_vars))

    monkeypatch.setattr(restore, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(restore, "build_context_from_config", lambda cfg: ctx)
    monkeypatch.setattr(
        restore, "read_manifest",
        lambda path: {"databases": {"faden_backend": {}}, "volumes": [], "env_snapshot": ".env.bak"},
    )
    monkeypatch.setattr(restore, "verify_backup", lambda path, manifest: [])
    monkeypatch.setattr(restore, "service_names", lambda ctx: ["nginx", "postgres", "rabbitmq", "backend"])
    monkeypatch.setattr(restore, "compose_stop", lambda *a, **k: 0)
    monkeypatch.setattr(restore, "compose_restart", lambda *a, **k: 0)
    monkeypatch.setattr(restore, "compose_up", up)
    monkeypatch.setattr(restore, "compose_ps", lambda ctx: [])
    monkeypatch.setattr(restore, "prepare_restore", lambda *a, **k: Prepared(["faden_backend"], {"faden_backend": 1}, [], []))
    monkeypatch.setattr(restore_swap, "swap_in", lambda *a, **k: [restore_swap.Swapped("faden_backend", True)])
    monkeypatch.setattr(restore_swap, "swap_back", lambda *a, **k: [])
    monkeypatch.setattr(restore_swap, "drop_old", lambda *a, **k: [])
    monkeypatch.setattr(
        restore, "keep_before_restore",
        lambda *a, **k: Kept(work_dir=tmp_path / "kept", env_path=root / ".env", env=LIVE_ENV.encode()),
    )
    monkeypatch.setattr(restore, "restore_env", fake_restore_env)
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


def test_a_restore_whose_services_do_not_start_goes_back(tmp_path: Path, install: SimpleNamespace) -> None:
    install.started = 1

    result = _run(tmp_path)
    text = " ".join(result.output.split())

    assert result.exit_code == 1
    assert "Restore complete." not in text
    assert "did not become healthy" in text and "went back" in text
    assert (install.root / ".env").read_text(encoding="utf-8") == LIVE_ENV
    written = [step[2]["RABBITMQ_API_PASS"] for step in install.steps if step[0] == "rabbitmq users"]
    assert written == ["restored-api", "live-api"], "RabbitMQ's users written again from the .env before"
