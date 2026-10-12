"""Where the local node's agent comes from.

The agent lives in the core repository (agent/) and is released from there; the
standalone faden-node-agent repository is retired. A development workspace runs
the checkout's own agent/; a release install takes the released agent through
the backend's install line, which checks the SHA-256 this core pins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from faden_cli.core import local_node

KW: dict[str, Any] = {
    "branch": "main",
    "backend_url": "http://127.0.0.1:8000",
    "advertise_host": "",
    "enrollment_token": "tok",
    "remote_host": None,
    "use_sudo": False,
}


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    for name in ("_provision_local", "_provision_local_binary", "_provision_release",
                 "_provision_remote", "_provision_remote_release"):
        monkeypatch.setattr(local_node, name, lambda name=name, **_kw: seen.append(name) or True)
    return seen


def _checkout(workspace: Path) -> Path:
    agent = workspace / "fadenstack" / "agent"
    agent.mkdir(parents=True)
    (agent / "pyproject.toml").write_text("[project]\nname = 'faden-agent'\n", encoding="utf-8")
    return agent


def test_no_retired_repository_is_named() -> None:
    assert not hasattr(local_node, "_NODE_AGENT_REPO")
    assert "faden-node-agent" not in Path(local_node.__file__).read_text(encoding="utf-8")


def test_development_runs_the_checkouts_own_agent(tmp_path: Path, calls: list[str]) -> None:
    _checkout(tmp_path)
    assert local_node.provision_local_node_agent(workspace=tmp_path, **KW)
    assert calls == ["_provision_local"]


def test_development_prefers_the_checkouts_own_build(tmp_path: Path, calls: list[str]) -> None:
    dist = _checkout(tmp_path) / "dist"
    dist.mkdir()
    (dist / "faden-agent").write_bytes(b"ELF")
    local_node.provision_local_node_agent(workspace=tmp_path, **KW)
    assert calls == ["_provision_local_binary"]


def test_deploy_run_in_the_checkout_itself_runs_its_agent(tmp_path: Path, calls: list[str]) -> None:
    """``faden deploy`` in the checkout passes the checkout, not the workspace above it."""
    _checkout(tmp_path)
    local_node.provision_local_node_agent(workspace=tmp_path / "fadenstack", **KW)
    assert calls == ["_provision_local"]


def test_a_release_install_takes_the_released_agent(tmp_path: Path, calls: list[str]) -> None:
    (tmp_path / "fadenstack" / "agent").mkdir(parents=True)  # a directory of that name is no checkout
    local_node.provision_local_node_agent(workspace=tmp_path, **KW)
    assert calls == ["_provision_release"]


def test_remote_follows_the_same_choice(tmp_path: Path, calls: list[str]) -> None:
    local_node.provision_local_node_agent(workspace=tmp_path, **{**KW, "remote_host": "u@h"})
    _checkout(tmp_path)
    local_node.provision_local_node_agent(workspace=tmp_path, **{**KW, "remote_host": "u@h"})
    assert calls == ["_provision_remote_release", "_provision_remote"]


def test_a_checkout_is_never_cloned_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from the checkout must not run git against it."""
    agent = _checkout(tmp_path)
    ran: list[list[str]] = []
    monkeypatch.setattr(local_node, "_install_agent_dependencies", lambda _d: True)
    monkeypatch.setattr(local_node, "_start_agent_foreground", lambda d, _e: ran.append(["start", str(d)]) or True)
    monkeypatch.setattr(local_node, "_run", lambda cmd, **_kw: ran.append(cmd) or 0)
    monkeypatch.setattr(local_node.platform, "system", lambda: "Windows")
    assert local_node.provision_local_node_agent(workspace=tmp_path, **KW)
    assert ran == [["start", str(agent)]]


def test_the_release_path_runs_the_install_line_with_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_node.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_node, "_fetch_install_line", lambda url: "#!/bin/sh\n")
    run: list[list[str]] = []
    monkeypatch.setattr(local_node.subprocess, "run", lambda cmd, **_kw: run.append(cmd) or type("P", (), {"returncode": 0})())
    assert local_node._provision_release(
        backend_url="http://b:8000", advertise_host="", enrollment_token="tok", use_sudo=False
    )
    assert run[0][0] == "sh"
    assert run[0][2:] == ["--token", "tok", "--backend", "http://b:8000", "--user"]


def test_on_https_the_install_line_gets_the_installs_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", encoding="ascii")
    monkeypatch.setattr(local_node.platform, "system", lambda: "Linux")
    monkeypatch.setattr(local_node, "_fetch_install_line", lambda url: "#!/bin/sh\n")
    run: list[list[str]] = []
    monkeypatch.setattr(local_node.subprocess, "run", lambda cmd, **_kw: run.append(cmd) or type("P", (), {"returncode": 0})())
    assert local_node._provision_release(
        backend_url="https://localhost", advertise_host="", enrollment_token="tok", use_sudo=True, ca_file=ca
    )
    assert run[0][2:] == ["--token", "tok", "--backend", "https://localhost", "--ca-file", str(ca)]


def test_a_remote_host_gets_the_authority_in_the_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", encoding="ascii")
    monkeypatch.setattr(local_node.shutil, "which", lambda _name: "ssh")
    sent: list[str] = []

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        sent.append(kw["input"])
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(local_node.subprocess, "run", fake_run)
    assert local_node._provision_remote_release(
        remote_host="u@h", backend_url="https://ai.example.internal", advertise_host="",
        enrollment_token="tok", use_sudo=True, ca_file=ca,
    )
    (script,) = sent
    assert "MIIB" in script
    assert 'curl -fsSL --cacert "$CA"' in script
    assert script.rstrip().endswith('--ca-file "$CA"')


def test_a_release_install_off_linux_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_node.platform, "system", lambda: "Windows")
    assert not local_node._provision_release(
        backend_url="http://b", advertise_host="", enrollment_token="t", use_sudo=False
    )
