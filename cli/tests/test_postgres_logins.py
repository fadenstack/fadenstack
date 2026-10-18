"""No Postgres login with a fixed password in an install.

shared/initdb made ``llm_user`` and ``faden_backend`` with the same passwords on every install, and let
``llm_user`` read every backend table. Nothing in an install signs in with them. A new database no longer gets
them; deploy and upgrade switch them off where an older install has them; the development stack makes the one
it needs itself and reads the gateway's tables with the shared login.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from faden_cli.commands import deploy
from faden_cli.core.registry import backend_dev_env_for

INITDB = Path(__file__).resolve().parents[2] / "shared" / "initdb"


def test_a_new_database_gets_no_login() -> None:
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(INITDB.glob("*.sql"))).upper()
    statements = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    assert not any("CREATE ROLE" in line or "PASSWORD" in line for line in statements)
    for db in ("LANGFUSE", "FADEN_BACKEND", "FADEN_GATEWAY", "FADEN_MCP", "FADEN_SKILLS", "FADEN_PROVENANCE"):
        assert f"CREATE DATABASE {db};" in sql


class _Ran:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> _Ran:
    fake = _Ran("llm_user,faden_backend\nDO\n")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr("shutil.which", lambda _name: "docker")
    return fake


def test_an_older_install_has_them_switched_off(tmp_path: Path, ran: _Ran) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_USER=postgres\nFADEN_BACKEND_ENVIRONMENT=production\n", encoding="utf-8")

    assert deploy.retire_fixed_logins(env) == ["llm_user", "faden_backend"]

    (call,) = ran.calls
    assert call["cmd"][:3] == ["docker", "exec", "-i"] and "faden-postgres" in call["cmd"]
    assert "ALTER ROLE %I NOLOGIN" in call["input"]
    assert "'llm_user'" in call["input"] and "'faden_backend'" in call["input"]


def test_a_development_stack_keeps_its_login(tmp_path: Path, ran: _Ran) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_USER=postgres\nFADEN_BACKEND_ENVIRONMENT=dev\n", encoding="utf-8")

    assert deploy.retire_fixed_logins(env) == []
    assert ran.calls == []


def test_an_env_that_does_not_say_production_is_left_alone(tmp_path: Path, ran: _Ran) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_USER=postgres\n", encoding="utf-8")

    assert deploy.retire_fixed_logins(env) == []
    assert ran.calls == []


def test_the_install_s_own_login_is_never_switched_off(tmp_path: Path, ran: _Ran) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_USER=faden_backend\nFADEN_BACKEND_ENVIRONMENT=production\n", encoding="utf-8")

    deploy.retire_fixed_logins(env)

    assert "'faden_backend'" not in ran.calls[0]["input"]


def test_the_dev_backend_reads_the_gateway_tables_with_the_shared_login(tmp_path: Path) -> None:
    shared = tmp_path / ".env"
    shared.write_text("POSTGRES_USER=postgres\nPOSTGRES_PASSWORD=devpassword\n", encoding="utf-8")

    env = backend_dev_env_for(shared)

    assert env["FADEN_BACKEND_LLM_GRAPH_DB_USER"] == "postgres"
    assert env["FADEN_BACKEND_LLM_GRAPH_DB_PASS"] == "devpassword"  # noqa: S105 - the dev value
