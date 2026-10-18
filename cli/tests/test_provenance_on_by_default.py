"""Provenance is on by default (decision D11): content the platform generates is marked as AI-generated.

``faden deploy`` switches it on for a new install, and ``faden upgrade`` for an existing one, unless an
administrator switched it off: ``faden module disable provenance`` says what that loses, asks, and records in
faden.yaml who did it and when. ``faden module enable provenance`` removes the record.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from faden_cli.commands import deploy
from faden_cli.commands import module as module_cmd
from faden_cli.commands import upgrade as upgrade_module
from faden_cli.core import bootstrap, clickhouse, compose
from faden_cli.core.env_gen import read_env_file
from faden_cli.core.registry import MODULES
from faden_cli.core.settings import FadenConfig

TOKENS = MODULES["provenance"].service_token_keys
OFF = {"provenance": {"by": "ops", "at": "2026-10-01T09:00:00+00:00"}}


def _said(result: Any) -> str:
    """The command's output in one line: the console wraps long lines."""
    return " ".join(result.output.split())


# ── Deploy ────────────────────────────────────────────────────────


def test_a_new_install_runs_it() -> None:
    assert deploy.deploy_profiles(FadenConfig(), "") == {"provenance"}
    assert deploy.deploy_profiles(FadenConfig(), "pii") == {"pii", "provenance"}


def test_a_deploy_leaves_it_off_where_an_administrator_switched_it_off() -> None:
    cfg = FadenConfig(profiles=["mcp"], modules_switched_off=dict(OFF))
    assert deploy.deploy_profiles(cfg, "") == {"mcp"}
    assert cfg.modules_switched_off == OFF


def test_asked_for_by_name_it_runs_and_the_record_goes() -> None:
    cfg = FadenConfig(modules_switched_off=dict(OFF))
    assert deploy.deploy_profiles(cfg, "provenance") == {"provenance"}
    assert cfg.modules_switched_off == {}


def test_only_modules_on_by_default_are_switched_on() -> None:
    assert module_cmd.default_modules(FadenConfig()) == ["provenance"]
    assert module_cmd.default_modules(FadenConfig(profiles=["provenance"])) == []
    assert module_cmd.default_modules(FadenConfig(modules_switched_off=dict(OFF))) == []


# ── faden module disable / enable ────────────────────────────────


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An install with provenance on, and the config that points at it."""
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / ".env").write_text(
        "POSTGRES_PASSWORD=keep-me\nFADEN_GATEWAY_PROVENANCE_ENABLED=true\nFADEN_BACKEND_PROVENANCE_ENABLED=true\n",
        encoding="utf-8",
    )
    config = tmp_path / "faden.yaml"
    config.write_text(
        yaml.safe_dump({"install_dir": str(shared), "profiles": ["provenance"]}), encoding="utf-8",
    )
    monkeypatch.setenv("FADEN_CONFIG", str(config))
    monkeypatch.setattr(module_cmd.getpass, "getuser", lambda: "ops")
    return shared


def _config(install: Path) -> dict[str, Any]:
    return yaml.safe_load((install.parent / "faden.yaml").read_text(encoding="utf-8"))


def test_switching_it_off_warns_asks_and_records_who_and_when(install: Path) -> None:
    result = CliRunner().invoke(module_cmd.module_group, ["disable", "provenance"], input="y\n")

    assert result.exit_code == 0, result.output
    said = _said(result)
    assert "no longer be marked as AI-generated (EU AI Act Art. 50(2))" in said
    assert "Switch off provenance?" in said
    config = _config(install)
    assert config["profiles"] == []
    record = config["modules_switched_off"]["provenance"]
    assert record["by"] == "ops"
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    env = read_env_file(install / ".env")
    assert env["FADEN_GATEWAY_PROVENANCE_ENABLED"] == env["FADEN_BACKEND_PROVENANCE_ENABLED"] == "false"


def test_declined_nothing_changes(install: Path) -> None:
    before_env = (install / ".env").read_text(encoding="utf-8")

    result = CliRunner().invoke(module_cmd.module_group, ["disable", "pii", "provenance"], input="n\n")

    assert result.exit_code == 1
    assert _config(install)["profiles"] == ["provenance"]
    assert "modules_switched_off" not in _config(install)
    assert (install / ".env").read_text(encoding="utf-8") == before_env


def test_yes_switches_it_off_without_asking(install: Path) -> None:
    result = CliRunner().invoke(module_cmd.module_group, ["disable", "provenance", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Art. 50(2)" in _said(result), "still said"
    assert "Switch off provenance?" not in _said(result)
    assert _config(install)["modules_switched_off"]["provenance"]["by"] == "ops"


def test_another_module_is_switched_off_without_a_question_or_a_record(install: Path) -> None:
    result = CliRunner().invoke(module_cmd.module_group, ["disable", "pii"])

    assert result.exit_code == 0, result.output
    assert "Switch off" not in _said(result)
    assert not _config(install).get("modules_switched_off")


def test_switching_it_on_again_removes_the_record(install: Path) -> None:
    CliRunner().invoke(module_cmd.module_group, ["disable", "provenance", "--yes"])
    listed = CliRunner().invoke(module_cmd.module_group, ["list"])
    assert "by ops" in _said(listed)

    result = CliRunner().invoke(module_cmd.module_group, ["enable", "provenance"])

    assert result.exit_code == 0, result.output
    config = _config(install)
    assert config["profiles"] == ["provenance"]
    assert config["modules_switched_off"] == {}
    assert read_env_file(install / ".env")["FADEN_GATEWAY_PROVENANCE_ENABLED"] == "true"


def test_without_a_login_name_the_account_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_name() -> str:
        raise OSError("no login name")

    monkeypatch.setattr(module_cmd.getpass, "getuser", no_name)
    monkeypatch.setenv("USERNAME", "svc-faden")
    assert module_cmd.record_switched_off(FadenConfig(), "provenance")["by"] == "svc-faden"


# ── Upgrade ───────────────────────────────────────────────────────


@pytest.fixture
def older_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An install from before provenance, and an upgrade that reaches no Docker."""
    shared = tmp_path / "shared"
    (shared / "rabbitmq").mkdir(parents=True)
    (shared / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (shared / ".env").write_text(
        "RABBITMQ_ADMIN_USER=admin\nRABBITMQ_ADMIN_PASS=adminpw\nRABBITMQ_BACKEND_PASS=backendpw\n"
        "RABBITMQ_API_PASS=apipw\n",
        encoding="utf-8",
    )
    config = tmp_path / "faden.yaml"
    config.write_text(
        f"version: 1\ninstall_dir: {shared.as_posix()}\ncompose_file: docker-compose.yaml\nprofiles: [mcp]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FADEN_CONFIG", str(config))
    monkeypatch.setattr(compose, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(compose, "foreign_containers", lambda ctx: [])
    monkeypatch.setattr(upgrade_module, "compose_up", lambda *a, **k: 0)
    monkeypatch.setattr(clickhouse, "drop_stale_logs", lambda ctx: [])
    monkeypatch.setattr(bootstrap, "wait_for_backend", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "_sync_postgres_password", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "retire_fixed_logins", lambda *a, **k: [])
    return shared


def _upgrade(*extra: str) -> Any:
    flags = ["-y", "--no-backup", "--no-build", "--skip-doctor", *extra]
    return CliRunner().invoke(upgrade_module.upgrade_cmd, flags)


def test_an_upgrade_switches_it_on(older_install: Path) -> None:
    shared = older_install

    result = _upgrade()

    assert result.exit_code == 0, result.output
    assert "Switched on provenance" in _said(result)
    assert _config(shared)["profiles"] == ["mcp", "provenance"]
    env = read_env_file(shared / ".env")
    assert env["FADEN_GATEWAY_PROVENANCE_ENABLED"] == env["FADEN_BACKEND_PROVENANCE_ENABLED"] == "true"
    assert env["FADEN_PROVENANCE_SERVICE_TOKEN"]
    assert len({env[key] for key in TOKENS}) == 1, "one token on all three sides"
    assert env["RABBITMQ_API_PASS"] == "apipw", "the rest of .env is kept"


def test_an_upgrade_leaves_it_off_where_an_administrator_switched_it_off(older_install: Path) -> None:
    shared = older_install
    config = shared.parent / "faden.yaml"
    config.write_text(config.read_text(encoding="utf-8") + yaml.safe_dump({"modules_switched_off": OFF}),
                      encoding="utf-8")

    result = _upgrade()

    assert result.exit_code == 0, result.output
    assert _config(shared)["profiles"] == ["mcp"]
    assert _config(shared)["modules_switched_off"] == OFF
    env = read_env_file(shared / ".env")
    assert env.get("FADEN_GATEWAY_PROVENANCE_ENABLED") != "true"
    assert env["FADEN_PROVENANCE_SERVICE_TOKEN"], "the compose file asks for its token all the same"


def test_a_dry_run_says_it_would_switch_it_on_and_changes_nothing(older_install: Path) -> None:
    shared = older_install
    before = (shared / ".env").read_text(encoding="utf-8")

    result = _upgrade("--dry-run")

    assert result.exit_code == 0, result.output
    assert "Switched on: provenance (on by default)" in _said(result)
    assert _config(shared)["profiles"] == ["mcp"]
    assert (shared / ".env").read_text(encoding="utf-8") == before

