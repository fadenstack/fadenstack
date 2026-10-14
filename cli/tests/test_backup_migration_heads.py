"""A backup reads the migration heads of the modules this install runs, and never pulls an image for it."""

from __future__ import annotations

import subprocess

import pytest

from faden_cli.core import backup
from faden_cli.core.compose import ComposeContext


class FakeDocker:
    def __init__(self, services: str | None) -> None:
        self.services = services
        self.runs: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "config" in cmd:
            if self.services is None:
                return subprocess.CompletedProcess(cmd, 1, "", "no compose file")
            return subprocess.CompletedProcess(cmd, 0, self.services, "")
        self.runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "a1b2c3d4e5f6 (head)\n", "")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch):
    def make(services: str | None) -> FakeDocker:
        fake = FakeDocker(services)
        monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
        monkeypatch.setattr(backup.subprocess, "run", fake)
        return fake

    return make


def test_switched_off_modules_are_not_run_and_nothing_is_pulled(docker) -> None:
    fake = docker("faden-backend\nfaden-backend-migrator\nfaden-gateway-migrator\nfaden-pii-migrator\n")
    heads = backup.get_migration_heads(ComposeContext(profiles=["pii"]))
    ran = [cmd[cmd.index("never") + 1] for cmd in fake.runs]
    assert ran == ["faden-backend-migrator", "faden-gateway-migrator", "faden-pii-migrator"]
    assert all(cmd[cmd.index("--pull") + 1] == "never" for cmd in fake.runs)
    assert heads == {"faden_backend": "a1b2c3d4e5f6", "faden_gateway": "a1b2c3d4e5f6", "faden_mcp": "",
                     "faden_skills": "", "pii": "a1b2c3d4e5f6"}


def test_without_the_service_list_every_migrator_is_asked_but_none_pulls(docker) -> None:
    fake = docker(None)
    backup.get_migration_heads(ComposeContext(profiles=[]))
    assert len(fake.runs) == len(backup._MIGRATOR_SERVICES)
    assert all("--pull" in cmd and cmd[cmd.index("--pull") + 1] == "never" for cmd in fake.runs)
