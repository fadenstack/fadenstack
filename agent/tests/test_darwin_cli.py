"""The CLI on a Mac: root only, paths before the join, the job only after approval.

``darwin_service`` itself is covered in test_darwin_service.py; here its
functions are replaced, and what is checked is the order the CLI calls them in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from faden_agent import __main__ as agent_main
from faden_agent import darwin_service as ds


@pytest.fixture()
def on_a_mac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """A Mac run as root, with every machine-changing step recorded instead of done."""
    events: list[str] = []
    layout = ds.Layout(tmp_path / "root")
    monkeypatch.setattr(agent_main, "_IS_DARWIN", True)
    monkeypatch.setattr(agent_main.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ds, "Layout", lambda: layout)
    monkeypatch.setattr(ds, "Runner", lambda: object())
    identity = ds.Identity(ds.SERVICE_USER, 452, 452)

    def ensure(*_a: Any, **_k: Any) -> ds.Identity:
        events.append("account")
        return identity

    monkeypatch.setattr(ds, "ensure_account", ensure)
    monkeypatch.setattr(ds, "prepare_files", lambda *_a, **_k: events.append("files"))
    monkeypatch.setattr(ds, "probe_state_dir", lambda *_a, **_k: events.append("probe"))
    monkeypatch.setattr(ds, "activate", lambda *_a, **_k: events.append("activate"))
    monkeypatch.setattr(ds, "write_env_file", lambda _r, lay, content: (events.append("env"), lay.env_file)[1])
    monkeypatch.setattr(ds, "running_binary", lambda: tmp_path / "faden-agent")
    for key in ("STATE_PATH", "MODEL_STORE", "BACKEND_URL", "AGENT_ID", "HOST", "ENROLLMENT_TOKEN"):
        monkeypatch.delenv(f"FADEN_NODE_AGENT_{key}", raising=False)
    return events


def test_the_join_is_filed_only_once_the_service_paths_are_proven(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, str] = {}

    async def not_member(_config: Any) -> None:
        return None

    async def join(config: Any) -> bool:
        on_a_mac.append("join")
        seen["state"] = str(config.state_path)
        return True

    monkeypatch.setattr(agent_main, "_existing_membership", not_member)
    monkeypatch.setattr(agent_main, "_join_flow", join)
    agent_main.cmd_join("http://192.0.2.220:8000")
    assert on_a_mac.index("probe") < on_a_mac.index("join") < on_a_mac.index("activate")
    assert seen["state"] == str(tmp_path / "root/var/db/fadenstack/agent/state.json")


def test_a_refused_or_timed_out_join_never_loads_the_job(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_member(_config: Any) -> None:
        return None

    async def refused(_config: Any) -> bool:
        return False

    monkeypatch.setattr(agent_main, "_existing_membership", not_member)
    monkeypatch.setattr(agent_main, "_join_flow", refused)
    with pytest.raises(SystemExit):
        agent_main.cmd_join("http://192.0.2.220:8000")
    assert "activate" not in on_a_mac


def test_a_member_running_the_line_again_is_upgraded_without_a_join(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def member(_config: Any) -> dict:
        return {"agent_id": "mac-01"}

    async def must_not_join(_config: Any) -> bool:
        raise AssertionError("a member filed a join")

    monkeypatch.setattr(agent_main, "_existing_membership", member)
    monkeypatch.setattr(agent_main, "_join_flow", must_not_join)
    agent_main.cmd_join("http://192.0.2.220:8000")
    assert on_a_mac[-1] == "activate"


def test_start_with_a_token_saves_it_for_the_service_and_activates(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, str] = {}

    def save(env: dict[str, str]) -> Path:
        saved.update(env)
        return Path("/dev/null")

    monkeypatch.setattr(agent_main, "_save_env_file", save)
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "http://192.0.2.220:8000")
    monkeypatch.setenv("FADEN_NODE_AGENT_ENROLLMENT_TOKEN", "tok")
    agent_main.cmd_start()
    assert saved["FADEN_NODE_AGENT_ENROLLMENT_TOKEN"] == "tok"
    assert saved["FADEN_NODE_AGENT_STATE_PATH"].endswith("/var/db/fadenstack/agent/state.json")
    assert on_a_mac[-1] == "activate"


@pytest.mark.parametrize("command", ["join", "start", "stop", "uninstall"])
def test_without_root_a_mac_asks_for_sudo(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    monkeypatch.setattr(agent_main.os, "geteuid", lambda: 501)
    run = {
        "join": lambda: agent_main.cmd_join("http://192.0.2.220:8000"),
        "start": agent_main.cmd_start,
        "stop": agent_main.cmd_stop,
        "uninstall": agent_main.cmd_uninstall,
    }[command]
    with pytest.raises(SystemExit) as exc:
        run()
    assert exc.value.code == 1
    assert "run this with sudo" in capsys.readouterr().out
    assert on_a_mac == []


def test_a_user_service_is_refused_on_a_mac(on_a_mac: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        agent_main.cmd_start(user=True)
    assert exc.value.code == 2
    assert on_a_mac == []


def test_a_source_install_is_not_made_a_service(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ds, "running_binary", lambda: None)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: Path("/dev/null"))
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "http://192.0.2.220:8000")
    with pytest.raises(SystemExit):
        agent_main.cmd_start()
    assert "not a source install" in capsys.readouterr().out
    assert "activate" not in on_a_mac


def test_uninstall_reminds_to_remove_the_machine_in_the_console(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ds, "uninstall", lambda *_a, **_k: on_a_mac.append("uninstall"))
    agent_main.cmd_uninstall()
    out = capsys.readouterr().out
    assert "credential are deleted" in out
    assert "Remove this machine from Fadenstack" in out


def test_uninstall_is_mac_only() -> None:
    with pytest.raises(SystemExit) as exc:
        agent_main.cmd_uninstall()
    assert exc.value.code == 2


def test_linux_stop_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(agent_main, "_IS_WINDOWS", False)
    monkeypatch.setattr(agent_main, "_cmd_stop_linux", lambda: called.append("linux"))
    monkeypatch.setattr(agent_main, "_cmd_stop_darwin", lambda: called.append("darwin"))
    agent_main.cmd_stop()
    assert called == ["linux"]


def test_the_mac_env_file_is_the_services_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    layout = ds.Layout(tmp_path / "root")
    monkeypatch.setattr(agent_main, "_IS_DARWIN", True)
    monkeypatch.setattr(ds, "Layout", lambda: layout)
    assert agent_main._env_file_path() == layout.env_file
    layout.env_file.parent.mkdir(parents=True)
    layout.env_file.write_text("FADEN_NODE_AGENT_BACKEND_URL=http://b\n")
    assert agent_main._load_env_file() == {"FADEN_NODE_AGENT_BACKEND_URL": "http://b"}


def test_version_flag(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_main.sys, "argv", ["faden-agent", "--version"])
    with pytest.raises(SystemExit) as exc:
        agent_main.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"faden-agent {agent_main._AGENT_VERSION}"
