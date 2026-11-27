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


class FakeRunner:
    """Stands in for the join run as the service account."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.rc = 0
        #: Per-call statuses, consumed first (the check, then the join); ``rc`` afterwards.
        self.rcs: list[int] = []
        self.calls: list[dict[str, Any]] = []

    def call(self, argv: list[str], *, uid: int, gid: int, env: dict[str, str], cwd: str) -> int:
        self.events.append("join-as-service")
        self.calls.append({"argv": argv, "uid": uid, "gid": gid, "env": env, "cwd": cwd})
        return self.rcs.pop(0) if self.rcs else self.rc


@pytest.fixture()
def runner(on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner(on_a_mac)
    monkeypatch.setattr(ds, "Runner", lambda: fake)
    return fake


@pytest.fixture()
def on_a_mac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """A Mac run as root, with every machine-changing step recorded instead of done."""
    events: list[str] = []
    layout = ds.Layout(tmp_path / "root")
    monkeypatch.setattr(agent_main, "_IS_DARWIN", True)
    monkeypatch.setattr(agent_main.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ds, "Layout", lambda: layout)
    monkeypatch.setattr(ds, "Runner", lambda: FakeRunner(events))
    monkeypatch.setattr(ds, "stage_binary", lambda *_a, **_k: (events.append("stage"), "0.2.12-abc")[1])
    monkeypatch.delenv(agent_main._DARWIN_JOIN_CHILD, raising=False)
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


def _no_join_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(_config: Any) -> Any:
        raise AssertionError("root ran the join or read the state itself")

    monkeypatch.setattr(agent_main, "_existing_membership", never)
    monkeypatch.setattr(agent_main, "_join_flow", never)


def test_root_prepares_then_runs_the_join_as_the_service_then_activates(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_join_as_root(monkeypatch)
    saved: dict[str, str] = {}
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: (saved.update(env), Path("/dev/null"))[1])
    monkeypatch.setenv("FADEN_NODE_AGENT_ENROLLMENT_TOKEN", "stale")
    agent_main.cmd_join("http://192.0.2.220:8000")
    assert on_a_mac.index("probe") < on_a_mac.index("stage") < on_a_mac.index("join-as-service")
    assert on_a_mac.index("join-as-service") < on_a_mac.index("activate")
    call = runner.calls[-1]
    assert call["env"].get(agent_main._DARWIN_JOIN_CHECK) is None
    assert runner.calls[0]["env"][agent_main._DARWIN_JOIN_CHECK] == "1"
    assert (call["uid"], call["gid"]) == (452, 452)
    assert call["argv"] == [str(tmp_path / "root/Library/Fadenstack/agent/0.2.12-abc/faden-agent"), "join"]
    state_dir = str(tmp_path / "root/var/db/fadenstack/agent")
    assert call["cwd"] == call["env"]["HOME"] == state_dir
    assert call["env"]["FADEN_NODE_AGENT_STATE_PATH"] == f"{state_dir}/state.json"
    assert call["env"]["FADEN_NODE_AGENT_BACKEND_URL"] == "http://192.0.2.220:8000"
    assert call["env"][agent_main._DARWIN_JOIN_CHILD] == "1"
    assert "SUDO_USER" not in call["env"]
    assert "FADEN_NODE_AGENT_ENROLLMENT_TOKEN" not in saved


def test_a_refused_or_timed_out_join_never_loads_the_job(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_join_as_root(monkeypatch)
    runner.rc = 1
    with pytest.raises(SystemExit):
        agent_main.cmd_join("http://192.0.2.220:8000")
    assert "activate" not in on_a_mac


def test_a_member_running_the_line_again_is_upgraded_without_a_join(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_join_as_root(monkeypatch)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: Path("/dev/null"))
    runner.rc = agent_main._DARWIN_JOIN_MEMBER
    agent_main.cmd_join("http://192.0.2.220:8000")
    assert on_a_mac[-1] == "activate"


def _two_phase(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, check_rc: int, join_rc: int = 0
) -> None:
    _no_join_as_root(monkeypatch)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: Path("/dev/null"))
    monkeypatch.setattr(ds, "bootout", lambda _runner, **_k: on_a_mac.append("bootout"))
    runner.rcs = [check_rc, join_rc]
    agent_main.cmd_join("http://192.0.2.220:8000")


def test_not_a_member_stops_the_old_service_before_the_join_writes_state(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_phase(on_a_mac, runner, monkeypatch, agent_main._DARWIN_JOIN_NOT_MEMBER)
    tail = on_a_mac[on_a_mac.index("stage"):]
    assert tail[:4] == ["stage", "join-as-service", "bootout", "join-as-service"]
    assert tail[-1] == "activate"
    assert runner.calls[0]["env"][agent_main._DARWIN_JOIN_CHECK] == "1"
    assert agent_main._DARWIN_JOIN_CHECK not in runner.calls[1]["env"]


def test_unknown_membership_joins_without_stopping_a_possibly_healthy_agent(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_phase(on_a_mac, runner, monkeypatch, agent_main._DARWIN_JOIN_UNKNOWN)
    assert "bootout" not in on_a_mac
    assert len(runner.calls) == 2 and on_a_mac[-1] == "activate"


def test_member_neither_stops_nor_joins_just_starts(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_phase(on_a_mac, runner, monkeypatch, agent_main._DARWIN_JOIN_MEMBER)
    assert "bootout" not in on_a_mac
    assert len(runner.calls) == 1 and on_a_mac[-1] == "activate"


def test_a_failed_join_after_the_stop_never_starts(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit):
        _two_phase(on_a_mac, runner, monkeypatch, agent_main._DARWIN_JOIN_NOT_MEMBER, join_rc=1)
    assert "bootout" in on_a_mac and "activate" not in on_a_mac


class TestTheJoinAsTheService:
    """The child: what the staged build does when root runs it as _fadenagent."""

    @pytest.fixture(autouse=True)
    def _child(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(agent_main, "_IS_DARWIN", True)
        monkeypatch.setattr(agent_main.os, "geteuid", lambda: 452)
        monkeypatch.setenv(agent_main._DARWIN_JOIN_CHILD, "1")
        monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "http://192.0.2.220:8000")
        monkeypatch.setenv("FADEN_NODE_AGENT_STATE_PATH", str(tmp_path / "state.json"))

    def _exit(self) -> int | str | None:
        with pytest.raises(SystemExit) as exc:
            agent_main.cmd_join(None)
        return exc.value.code

    def test_files_the_join_with_the_service_state_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        seen: dict[str, str] = {}

        async def not_member(_config: Any) -> None:
            return None

        async def join(config: Any) -> bool:
            seen["state"] = str(config.state_path)
            return True

        monkeypatch.setattr(agent_main, "_existing_membership", not_member)
        monkeypatch.setattr(agent_main, "_join_flow", join)
        assert self._exit() == 0
        assert seen["state"] == str(tmp_path / "state.json")

    def test_a_member_says_so_with_its_own_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def member(_config: Any) -> dict:
            return {"agent_id": "mac-01"}

        monkeypatch.setattr(agent_main, "_existing_membership", member)
        assert self._exit() == agent_main._DARWIN_JOIN_MEMBER

    def test_check_mode_reports_not_a_member_without_joining(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def not_member(_config: Any) -> None:
            return None

        async def never(_config: Any) -> bool:
            raise AssertionError("check mode must not join")

        monkeypatch.setenv(agent_main._DARWIN_JOIN_CHECK, "1")
        monkeypatch.setattr(agent_main, "_existing_membership", not_member)
        monkeypatch.setattr(agent_main, "_join_flow", never)
        assert self._exit() == agent_main._DARWIN_JOIN_NOT_MEMBER

    def test_check_mode_reports_unknown_when_the_backend_cannot_say(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def unknown(_config: Any) -> None:
            raise agent_main._MembershipUnknown("unreachable")

        monkeypatch.setenv(agent_main._DARWIN_JOIN_CHECK, "1")
        monkeypatch.setattr(agent_main, "_existing_membership", unknown)
        assert self._exit() == agent_main._DARWIN_JOIN_UNKNOWN

    def test_check_mode_reports_a_member(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def member(_config: Any) -> dict:
            return {"agent_id": "mac-01"}

        monkeypatch.setenv(agent_main._DARWIN_JOIN_CHECK, "1")
        monkeypatch.setattr(agent_main, "_existing_membership", member)
        assert self._exit() == agent_main._DARWIN_JOIN_MEMBER

    def test_a_failed_join_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def not_member(_config: Any) -> None:
            return None

        async def refused(_config: Any) -> bool:
            return False

        monkeypatch.setattr(agent_main, "_existing_membership", not_member)
        monkeypatch.setattr(agent_main, "_join_flow", refused)
        assert self._exit() == 1

    def test_it_never_runs_as_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_main.os, "geteuid", lambda: 0)
        _no_join_as_root(monkeypatch)
        assert self._exit() == 2


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
    monkeypatch.setattr(ds, "uninstall", lambda *_a, **_k: (on_a_mac.append("uninstall"), True)[1])
    agent_main.cmd_uninstall()
    out = capsys.readouterr().out
    assert "credential are deleted" in out
    assert "Remove this machine from Fadenstack too: Machines → the machine → Delete machine." in out


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


def test_an_incomplete_uninstall_fails(
    on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ds, "uninstall", lambda *_a, **_k: False)
    with pytest.raises(SystemExit) as exc:
        agent_main.cmd_uninstall()
    assert exc.value.code == 1
    assert "not complete" in capsys.readouterr().out


_CHILD_ENV_ALLOWED = {
    agent_main._DARWIN_JOIN_CHILD, agent_main._DARWIN_JOIN_CHECK, "HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "TERM", "NO_COLOR",
}


def test_the_join_child_gets_none_of_roots_environment(
    on_a_mac: list[str], runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_join_as_root(monkeypatch)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: Path("/dev/null"))
    for key, value in {
        "SUDO_USER": "admin", "SUDO_UID": "501", "SUDO_COMMAND": "/bin/sh faden-agent.sh", "SUDO_GID": "20",
        "HOME": "/var/root", "USER": "root", "LOGNAME": "root", "MAIL": "/var/mail/root", "SHELL": "/bin/zsh",
        "SSH_AUTH_SOCK": "/private/tmp/agent.sock", "TMPDIR": "/var/folders/root/T/", "PYTHONPATH": "/evil",
        "DYLD_INSERT_LIBRARIES": "/evil.dylib", "PATH": "/opt/evil:/usr/bin",
    }.items():
        monkeypatch.setenv(key, value)
    agent_main.cmd_join("http://192.0.2.220:8000")
    env = runner.calls[0]["env"]
    assert set(env) - _CHILD_ENV_ALLOWED <= {k for k in env if k.startswith("FADEN_NODE_AGENT_")}
    assert not any(k.startswith(("SUDO_", "DYLD_", "PYTHON")) for k in env)
    assert env["USER"] == env["LOGNAME"] == ds.SERVICE_USER
    assert env["HOME"] == str(tmp_path / "root/var/db/fadenstack/agent")
    assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert env["TMPDIR"] == "/private/tmp"
    assert "root" not in {env["USER"], env["LOGNAME"]} and "/var/root" not in env.values()


def test_the_join_child_has_no_controlling_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """setsid: a process of the service's cannot push keystrokes into the admin's shell."""
    seen: dict[str, Any] = {}

    class Done:
        returncode = 0

    def run(argv: list[str], **kwargs: Any) -> Done:
        seen.update(kwargs)
        return Done()

    monkeypatch.setattr(ds.subprocess, "run", run)
    assert ds.Runner().call(["/x", "join"], uid=452, gid=452, env={}, cwd="/") == 0
    assert seen["start_new_session"] is True
    assert seen["stdin"] is ds.subprocess.DEVNULL
    assert seen["user"] == 452 and seen["group"] == 452 and seen["extra_groups"] == []
    assert "stdout" not in seen and "capture_output" not in seen, "the code must reach the terminal"


def test_start_forces_the_mac_model_store(on_a_mac: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A Linux path saved by an old configure must not survive: the service owns layout.models only."""
    saved: dict[str, str] = {}
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: (saved.update(env), Path("/dev/null"))[1])
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "http://192.0.2.220:8000")
    monkeypatch.setenv("FADEN_NODE_AGENT_MODEL_STORE", "/srv/faden/models")
    agent_main.cmd_start()
    assert saved["FADEN_NODE_AGENT_MODEL_STORE"].endswith("/var/db/fadenstack/agent/models")
