"""``faden rebuild``: the gateway's chat files are kept before anything is recreated."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from faden_cli.commands import rebuild
from faden_cli.core import backup


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, object]]:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    ctx = SimpleNamespace(env_file=env, base_cmd=lambda: ["docker", "compose"])
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(rebuild, "load_config", lambda: object())
    monkeypatch.setattr(rebuild, "build_context_from_config", lambda _cfg: ctx)
    monkeypatch.setattr(rebuild, "compose_build", lambda *_a, **_k: seen.append(("build", None)) or 0)
    monkeypatch.setattr(backup, "keep_chat_files", lambda env_file: seen.append(("keep_chat_files", env_file)))
    monkeypatch.setattr(
        "faden_cli.commands.deploy._sync_postgres_password", lambda *_a: seen.append(("password", None)),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_k: seen.append(("up", cmd)) or SimpleNamespace(returncode=0),
    )
    seen.append(("env", env))
    return seen


def test_the_gateway_keeps_its_chat_files_before_anything_is_recreated(steps: list[tuple[str, object]]) -> None:
    result = CliRunner().invoke(rebuild.rebuild_cmd, ["gateway"])
    assert result.exit_code == 0, result.output
    env = steps[0][1]
    names = [name for name, _ in steps[1:]]
    assert names == ["build", "keep_chat_files", "password", "up"]
    assert ("keep_chat_files", env) in steps


def test_another_service_leaves_the_chat_files_alone(steps: list[tuple[str, object]]) -> None:
    result = CliRunner().invoke(rebuild.rebuild_cmd, ["frontend"])
    assert result.exit_code == 0, result.output
    assert "keep_chat_files" not in [name for name, _ in steps]
