"""Grafana, Langfuse and RabbitMQ do not run on the administrator's console password.

``faden deploy`` set all three to it, and ``faden admin reset-password`` only changed the console's: after a reset
the services stayed on the old password, with nothing saying so. Each has a random password of its own in ``.env``
instead, and the reset touches the console only.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import admin
from faden_cli.commands.deploy import langfuse_login_env
from faden_cli.core.env_gen import default_env_vars


def test_a_fresh_env_gives_grafana_and_rabbitmq_random_passwords_of_their_own() -> None:
    one, other = default_env_vars(), default_env_vars()

    for key in ("GRAFANA_ADMIN_PASSWORD", "RABBITMQ_ADMIN_PASS"):
        assert len(one[key]) >= 16
        assert one[key] != other[key]


def test_langfuses_first_user_gets_the_admins_email_and_a_password_of_its_own() -> None:
    values = langfuse_login_env({"LANGFUSE_INIT_USER_PASSWORD": ""}, "ops@example.com")

    assert values["LANGFUSE_INIT_USER_EMAIL"] == "ops@example.com"
    assert len(values["LANGFUSE_INIT_USER_PASSWORD"]) >= 24
    assert values["LANGFUSE_INIT_PROJECT_SECRET_KEY"].startswith("sk-lf-")


def test_a_langfuse_password_already_set_is_kept() -> None:
    """Langfuse reads it only when it creates the user: another value in .env would not be the user's."""
    values = langfuse_login_env(
        {"LANGFUSE_INIT_USER_PASSWORD": "kept-password", "LANGFUSE_INIT_PROJECT_SECRET_KEY": "sk-lf-kept"},
        "ops@example.com",
    )

    assert "LANGFUSE_INIT_USER_PASSWORD" not in values
    assert "LANGFUSE_INIT_PROJECT_SECRET_KEY" not in values


def test_reset_password_changes_the_console_only(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="$2b$hash\nUPDATE 1\n", stderr="")

    monkeypatch.setattr(admin.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(admin.subprocess, "run", fake_run)

    result = CliRunner().invoke(admin.admin_group, ["reset-password", "-y", "--password", "a-long-enough-password"])

    assert result.exit_code == 0, result.output
    touched = {name for cmd in ran for name in cmd if name.startswith("faden-")}
    assert touched == {"faden-backend", "faden-postgres"}, "no other container is touched"
    assert "are not touched" in result.output.replace("\n", " ")
