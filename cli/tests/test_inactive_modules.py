"""A module that is switched off has its containers stopped and removed (data kept) by ``faden up``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from faden_cli.commands import up as up_command
from faden_cli.core import compose
from faden_cli.core.compose import ComposeContext

SERVICES = {
    "postgres": {},
    "backend": {},
    "faden-pii": {"profiles": ["pii"]},
    "faden-pii-worker": {"profiles": ["pii"]},
    "faden-mcp": {"profiles": ["mcp"]},
    "faden-base": {"profiles": ["_build"]},
}


class FakeCompose:
    """``docker compose`` answering config and ps, recording every call."""

    def __init__(self, existing: list[str]) -> None:
        self.existing = existing
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], *, capture: bool = False, check: bool = True, stream: bool = False):
        self.calls.append(cmd)
        out = ""
        if "config" in cmd:
            out = json.dumps({"name": "fadenstack", "services": SERVICES})
        elif "ps" in cmd:
            out = "\n".join(json.dumps({"Name": n, "Service": n, "State": "running"}) for n in self.existing)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def verbs(self) -> list[str]:
        return [c[next(i for i, w in enumerate(c) if w in ("config", "ps", "stop", "rm"))] for c in self.calls]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    def make(existing: list[str]) -> FakeCompose:
        f = FakeCompose(existing)
        monkeypatch.setattr(compose, "_run", f.run)
        monkeypatch.setattr(compose.shutil, "which", lambda _n: "docker")
        return f

    return make


def test_a_disabled_module_is_stopped_and_removed(fake) -> None:
    f = fake(["postgres", "backend", "faden-pii", "faden-pii-worker"])
    done = compose.stop_inactive_modules(ComposeContext(profiles=[]))
    assert done == [("pii", ["faden-pii", "faden-pii-worker"])]
    stop, rm = f.calls[-2], f.calls[-1]
    assert stop[-3:] == ["stop", "faden-pii", "faden-pii-worker"]
    assert rm[-4:] == ["rm", "-f", "faden-pii", "faden-pii-worker"]
    assert all(w not in c for c in f.calls for w in ("down", "-v", "--volumes"))


def test_an_enabled_module_is_left_running(fake) -> None:
    f = fake(["postgres", "faden-pii", "faden-pii-worker", "faden-mcp"])
    done = compose.stop_inactive_modules(ComposeContext(profiles=["pii"]))
    assert done == [("mcp", ["faden-mcp"])]
    assert "faden-pii" not in f.calls[-1] and "faden-pii" not in f.calls[-2]


def test_core_and_build_services_are_never_in_the_stop_list(fake) -> None:
    f = fake(["postgres", "backend", "faden-base"])
    assert compose.stop_inactive_modules(ComposeContext(profiles=[])) == []
    assert f.verbs() == ["config", "ps"]


def test_nothing_to_do_makes_no_docker_calls_beyond_the_lookup(fake) -> None:
    f = fake([])
    assert compose.stop_inactive_modules(ComposeContext(profiles=[])) == []
    assert f.verbs() == ["config", "ps"]


def test_all_modules_on_stops_nothing(fake) -> None:
    f = fake(["faden-pii", "faden-mcp"])
    assert compose.stop_inactive_modules(ComposeContext(profiles=["pii", "mcp"])) == []
    assert f.verbs() == ["config"]


def test_up_reports_each_stopped_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = SimpleNamespace(env_file=tmp_path / ".env")
    monkeypatch.setattr(up_command, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(up_command, "build_context_from_config", lambda _c: ctx)
    monkeypatch.setattr(up_command, "compose_up", lambda *_a, **_k: 0)
    monkeypatch.setattr("faden_cli.core.backup.keep_company_files", lambda *_a, **_k: None)
    monkeypatch.setattr(compose, "stop_inactive_modules", lambda _c: [("pii", ["faden-pii", "faden-pii-worker"])])
    result = CliRunner().invoke(up_command.up_cmd, [])
    assert result.exit_code == 0, result.output
    assert "PII is switched off: stopped and removed faden-pii, faden-pii-worker" in result.output


def test_up_with_named_services_does_not_stop_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = SimpleNamespace(env_file=tmp_path / ".env")
    monkeypatch.setattr(up_command, "load_config", lambda: SimpleNamespace(profiles=[]))
    monkeypatch.setattr(up_command, "build_context_from_config", lambda _c: ctx)
    monkeypatch.setattr(up_command, "compose_up", lambda *_a, **_k: 0)
    monkeypatch.setattr("faden_cli.core.backup.keep_company_files", lambda *_a, **_k: None)

    def boom(_c):
        raise AssertionError("a partial start must not stop modules")

    monkeypatch.setattr(compose, "stop_inactive_modules", boom)
    assert CliRunner().invoke(up_command.up_cmd, ["backend"]).exit_code == 0


def test_a_failed_stop_is_said_not_swallowed(fake, monkeypatch: pytest.MonkeyPatch) -> None:
    f = fake(["postgres", "faden-pii"])
    real = f.run

    def failing(cmd: list[str], **kw):
        done = real(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 1, "", "boom") if "stop" in cmd else done

    monkeypatch.setattr(compose, "_run", failing)
    said: list[str] = []
    monkeypatch.setattr(compose, "warning", said.append)
    assert compose.stop_inactive_modules(ComposeContext(profiles=[])) == []
    assert len(said) == 1 and "faden-pii" in said[0] and "docker rm -f faden-pii" in said[0]
