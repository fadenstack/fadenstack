"""``faden secrets rotate``: the order it does things in, and what a failure leaves behind.

The one rule: the previous key leaves ``.env`` only after every database has re-sealed and verified its
values. Everything before that must leave the install working with both keys, so a failure at any step is
repaired by running the command again.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from faden_cli.commands.secrets import secrets_group
from faden_cli.core import secret_rotation as rot
from faden_cli.core.env_gen import (
    ALLOW_PUBLISHED_KEYS,
    CREDENTIAL_PEPPER,
    PUBLISHED_DATA_KEYS,
    default_env_vars,
    read_env_file,
    rewrite_env,
    write_env_file,
)

MASTER = "FADEN_BACKEND_SETTINGS_MASTER_KEY"
MCP = "FADEN_MCP_ENCRYPTION_KEY"
CHAT = "FADEN_GATEWAY_ENCRYPTION_KEY"
OWN_MASTER = "own-master-key-aaaaaaaaaaaaaaaa"
OWN_MCP = "own-mcp-key-bbbbbbbbbbbbbbbbbbbb"
OWN_CHAT = "own-chat-key-cccccccccccccccccc"


def _install(tmp_path: Path, **overrides: str) -> Path:
    values = default_env_vars(admin_password="x")
    values.update({MASTER: OWN_MASTER, MCP: OWN_MCP, CHAT: OWN_CHAT, CREDENTIAL_PEPPER: "the-pepper-dddddddddddd"})
    values.update(overrides)
    env = tmp_path / ".env"
    write_env_file(env, values)
    return env


class FakeInstall:
    """The install, as a rotation sees it: a ``.env`` and a log of what was done to it, and in which state."""

    def __init__(self, env_path: Path, *, fail_job: str | None = None, unhealthy: list[str] | None = None,
                 backup_error: str | None = None, restart_ok: bool = True) -> None:
        self.env_path = env_path
        self.events: list[tuple[str, dict[str, bool]]] = []
        self.fail_job = fail_job
        self.sick = unhealthy or []
        self.backup_error = backup_error
        self.restart_ok = restart_ok
        self.in_maintenance = False
        self.maintenance_runs = 0

    def _note(self, what: str) -> None:
        env = read_env_file(self.env_path)
        self.events.append((what, {
            "previous": any(env.get(s.previous) for s in rot.SCOPES),
            "allow": bool(env.get(ALLOW_PUBLISHED_KEYS)),
        }))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def state(self, what: str, index: int = 0) -> dict[str, bool]:
        return [state for name, state in self.events if name == what][index]

    def unhealthy(self, services: list[str]) -> list[str]:
        self._note("health")
        return self.sick

    def backup(self) -> str:
        self._note("backup")
        if self.backup_error:
            raise rot.RotationError(self.backup_error)
        return "backups/20261012T100000Z"

    @contextmanager
    def maintenance(self):  # noqa: ANN201
        self.in_maintenance = True
        self.maintenance_runs += 1
        self._note("maintenance on")
        try:
            yield
        finally:
            self.in_maintenance = False
            self._note("maintenance off")

    def restart(self, services: list[str]) -> bool:
        assert self.in_maintenance, "services restart behind the maintenance page"
        self._note("restart:" + ",".join(services))
        return self.restart_ok

    def wait_until_healthy(self) -> bool:
        self._note("healthy")
        return True

    def run_job(self, job: rot.Job, *, skip_unreadable: bool) -> rot.JobResult:
        assert self.in_maintenance
        self._note("job:" + job.label)
        if self.fail_job == job.label:
            return rot.JobResult(ok=False, error="2 value(s) open with neither key")
        return rot.JobResult(ok=True, columns={"t.c": {"rotated": 3, "already_current": 0, "plaintext": 0, "unreadable": 0}})


def _run(install: FakeInstall, profiles: list[str] | None = None, only: list[str] | None = None,
         keys: list[str] | None = None, resume: bool = False) -> tuple[dict, list[str]]:
    said: list[str] = []
    plan = rot.make_plan(read_env_file(install.env_path), profiles or ["mcp"], only, resume=resume)
    supply = iter(keys or [f"new-key-{n}-eeeeeeeeeeeeeeee" for n in range(10)])
    results = rot.rotate(install, plan, new_key=lambda: next(supply), say=said.append)
    return results, said


# ── the order ─────────────────────────────────────────────────────


def test_the_steps_happen_in_the_order_that_keeps_the_install_openable(tmp_path: Path) -> None:
    install = FakeInstall(_install(tmp_path))
    _run(install)

    assert install.names() == [
        "health", "backup", "maintenance on",
        "restart:faden-backend,faden-backend-worker,faden-gateway,faden-mcp",
        "healthy",
        "job:backend database", "job:gateway database (provider keys)", "job:MCP database",
        "job:gateway database (chats)",
        "restart:faden-backend,faden-backend-worker,faden-gateway,faden-mcp", "healthy",
        "maintenance off",
    ]
    # The backup is taken before .env changes; the services restart with both keys; the jobs run while
    # the previous key is still there; it goes only after the last job.
    assert install.state("backup")["previous"] is False
    assert install.state("restart:faden-backend,faden-backend-worker,faden-gateway,faden-mcp", 0)["previous"] is True
    assert install.state("job:gateway database (chats)")["previous"] is True
    assert install.state("restart:faden-backend,faden-backend-worker,faden-gateway,faden-mcp", 1)["previous"] is False


def test_the_env_ends_with_new_keys_and_no_previous_ones(tmp_path: Path) -> None:
    env = _install(tmp_path)
    _run(FakeInstall(env), keys=["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"])
    after = read_env_file(env)
    assert (after[MASTER], after[MCP], after[CHAT]) == (
        "new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh",
    )
    assert not [s.previous for s in rot.SCOPES if s.previous in after]


def test_the_new_key_and_the_old_one_are_both_in_env_while_the_jobs_run(tmp_path: Path) -> None:
    env = _install(tmp_path)
    seen: dict[str, str] = {}

    class Watching(FakeInstall):
        def run_job(self, job, *, skip_unreadable):  # noqa: ANN001, ANN201
            seen.update(read_env_file(self.env_path))
            return super().run_job(job, skip_unreadable=skip_unreadable)

    _run(Watching(env), keys=["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"])
    assert seen[MASTER] == "new-master-ffffffffffff" and seen["FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"] == OWN_MASTER
    assert seen[MCP] == "new-mcp-gggggggggggggg" and seen["FADEN_MCP_ENCRYPTION_KEY_PREVIOUS"] == OWN_MCP
    assert seen[CHAT] == "new-chat-hhhhhhhhhhhhh" and seen["FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS"] == OWN_CHAT


# ── a failure in the middle ───────────────────────────────────────


def test_a_failing_job_keeps_the_previous_keys_and_stops_before_dropping_them(tmp_path: Path) -> None:
    env = _install(tmp_path)
    install = FakeInstall(env, fail_job="MCP database")
    with pytest.raises(rot.RotationError, match="neither key") as stopped:
        _run(install, keys=["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"])
    assert "--resume" in str(stopped.value)
    after = read_env_file(env)
    assert after["FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"] == OWN_MASTER
    assert after["FADEN_MCP_ENCRYPTION_KEY_PREVIOUS"] == OWN_MCP
    assert after[MASTER] == "new-master-ffffffffffff"
    assert install.names().count("restart:faden-backend,faden-backend-worker,faden-gateway,faden-mcp") == 1
    assert not install.in_maintenance, "the maintenance page is lifted however it ends"
    assert "job:gateway database (chats)" not in install.names()


def test_running_it_again_finishes_the_job_with_the_same_keys(tmp_path: Path) -> None:
    env = _install(tmp_path)
    with pytest.raises(rot.RotationError):
        _run(FakeInstall(env, fail_job="MCP database"),
             keys=["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"])
    mid = read_env_file(env)

    again = FakeInstall(env)
    asked: list[int] = []
    plan = rot.make_plan(read_env_file(env), ["mcp"], None)
    assert plan.mode == "resume" and [s.name for s in plan.scopes] == ["settings", "mcp", "chat"]
    results = rot.rotate(again, plan, new_key=lambda: asked.append(1) or "never", say=lambda _: None)  # type: ignore[func-returns-value]

    assert asked == [], "a resume never makes new keys"
    after = read_env_file(env)
    assert (after[MASTER], after[MCP], after[CHAT]) == (mid[MASTER], mid[MCP], mid[CHAT])
    assert not [s.previous for s in rot.SCOPES if s.previous in after]
    assert "health" not in again.names(), "a stopped install must be able to start again"
    assert results


def test_resume_without_anything_to_resume_says_so(tmp_path: Path) -> None:
    env = _install(tmp_path)
    with pytest.raises(rot.RotationError, match="No rotation is unfinished"):
        rot.make_plan(read_env_file(env), ["mcp"], None, resume=True)


def test_a_failed_restart_keeps_the_previous_keys(tmp_path: Path) -> None:
    env = _install(tmp_path)
    with pytest.raises(rot.RotationError, match="did not restart"):
        _run(FakeInstall(env, restart_ok=False))
    assert read_env_file(env)["FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"] == OWN_MASTER


def test_a_resume_asked_for_one_key_finishes_the_unfinished_one_first(tmp_path: Path) -> None:
    env = _install(tmp_path, FADEN_MCP_ENCRYPTION_KEY_PREVIOUS="old-mcp-iiiiiiiiiiiii")
    plan = rot.make_plan(read_env_file(env), ["mcp"], ["settings"])
    assert plan.mode == "resume"
    assert [s.name for s in plan.scopes] == ["mcp"]
    assert [s.name for s in plan.deferred] == ["settings"]


# ── before anything changes ───────────────────────────────────────


def test_unhealthy_services_stop_the_rotation_before_the_backup(tmp_path: Path) -> None:
    env = _install(tmp_path)
    before = env.read_text(encoding="utf-8")
    install = FakeInstall(env, unhealthy=["faden-gateway (unhealthy)"])
    with pytest.raises(rot.RotationError, match="faden-gateway"):
        _run(install)
    assert install.names() == ["health"]
    assert env.read_text(encoding="utf-8") == before


def test_no_backup_no_rotation(tmp_path: Path) -> None:
    env = _install(tmp_path)
    before = env.read_text(encoding="utf-8")
    with pytest.raises(rot.RotationError, match="backup failed"):
        _run(FakeInstall(env, backup_error="The backup failed, so nothing was changed: no space left"))
    assert env.read_text(encoding="utf-8") == before


# ── the pepper ────────────────────────────────────────────────────


def test_the_pepper_is_never_changed(tmp_path: Path) -> None:
    env = _install(tmp_path)
    _run(FakeInstall(env))
    assert read_env_file(env)[CREDENTIAL_PEPPER] == "the-pepper-dddddddddddd"


def test_an_env_without_a_pepper_gets_the_old_master_key_before_the_master_key_changes(tmp_path: Path) -> None:
    env = _install(tmp_path)
    rewrite_env(env, set_values={}, remove=(CREDENTIAL_PEPPER,))
    _run(FakeInstall(env), keys=["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"])
    after = read_env_file(env)
    assert after[CREDENTIAL_PEPPER] == OWN_MASTER != after[MASTER]


def test_rotating_only_mcp_leaves_the_master_key_and_the_pepper_alone(tmp_path: Path) -> None:
    env = _install(tmp_path)
    install = FakeInstall(env)
    _run(install, only=["mcp"])
    after = read_env_file(env)
    assert after[MASTER] == OWN_MASTER and after[CHAT] == OWN_CHAT
    assert after[MCP] != OWN_MCP
    assert install.names().count("job:backend database") == 0
    assert "restart:faden-mcp" in install.names()


# ── the published-keys switch ─────────────────────────────────────


def test_the_switch_goes_when_no_published_key_is_left(tmp_path: Path) -> None:
    env = _install(tmp_path, **{MASTER: PUBLISHED_DATA_KEYS[MASTER], ALLOW_PUBLISHED_KEYS: "true"})
    _run(FakeInstall(env))
    assert ALLOW_PUBLISHED_KEYS not in read_env_file(env)


def test_the_switch_stays_while_another_key_is_still_published(tmp_path: Path) -> None:
    env = _install(tmp_path, **{MASTER: PUBLISHED_DATA_KEYS[MASTER], MCP: PUBLISHED_DATA_KEYS[MCP],
                                ALLOW_PUBLISHED_KEYS: "true"})
    _run(FakeInstall(env), only=["settings"])
    after = read_env_file(env)
    assert after[ALLOW_PUBLISHED_KEYS] == "true"
    assert after[MASTER] != PUBLISHED_DATA_KEYS[MASTER]
    assert after[MCP] == PUBLISHED_DATA_KEYS[MCP]


# ── what is rotated ───────────────────────────────────────────────


def test_mcp_needs_its_module_and_chat_needs_a_key(tmp_path: Path) -> None:
    env = _install(tmp_path, **{CHAT: ""})
    plan = rot.make_plan(read_env_file(env), [], None)
    assert [s.name for s in plan.scopes] == ["settings"]
    with pytest.raises(rot.RotationError, match="Nothing to rotate for: mcp"):
        rot.make_plan(read_env_file(env), [], ["mcp"])
    with pytest.raises(rot.RotationError, match="chat"):
        rot.make_plan(read_env_file(env), ["mcp"], ["chat"])


def test_every_key_is_rotated_by_default(tmp_path: Path) -> None:
    env = _install(tmp_path)
    assert [s.name for s in rot.make_plan(read_env_file(env), ["mcp"], None).scopes] == ["settings", "mcp", "chat"]


# ── nothing leaks, .env stays private ─────────────────────────────


def test_no_key_is_ever_said(tmp_path: Path) -> None:
    env = _install(tmp_path)
    keys = ["new-master-ffffffffffff", "new-mcp-gggggggggggggg", "new-chat-hhhhhhhhhhhhh"]
    _, said = _run(FakeInstall(env), keys=keys)
    spoken = "\n".join(said)
    for secret in [*keys, OWN_MASTER, OWN_MCP, OWN_CHAT, "the-pepper-dddddddddddd"]:
        assert secret not in spoken


@pytest.mark.skipif(os.name == "nt", reason="no owner-only bits on Windows")
def test_env_stays_owner_only_and_nothing_is_left_beside_it(tmp_path: Path) -> None:
    env = _install(tmp_path)
    _run(FakeInstall(env))
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_rewrite_env_replaces_in_place_and_keeps_every_other_line(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# a comment\nA=1\nB=2\n\nC=3\n", encoding="utf-8")
    rewrite_env(env, set_values={"B": "two", "D": "4"}, remove=("A",))
    text = env.read_text(encoding="utf-8")
    assert text.splitlines()[:4] == ["# a comment", "B=two", "", "C=3"]
    assert read_env_file(env) == {"B": "two", "C": "3", "D": "4"}
    assert "\r" not in text
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


# ── the job's answer ──────────────────────────────────────────────


def test_a_jobs_last_line_is_its_answer() -> None:
    out = 'progress\n{"ok": true, "columns": {"t.c": {"rotated": 2}}}\n'
    result = rot.parse_job_output(out, 0)
    assert result.ok and result.columns == {"t.c": {"rotated": 2}}


def test_a_failed_job_is_a_failure_even_if_it_says_ok() -> None:
    assert not rot.parse_job_output('{"ok": true}', 1).ok
    assert rot.parse_job_output('{"ok": false, "error": "x"}', 1).error == "x"


def test_a_job_without_an_answer_is_a_failure() -> None:
    result = rot.parse_job_output("", 125, "docker: no such service\n")
    assert not result.ok and "no such service" in result.error


# ── with Docker ───────────────────────────────────────────────────


def test_a_job_runs_as_a_one_off_container_of_the_services_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_cli.core import compose  # noqa: PLC0415

    seen: list[list[str]] = []

    def run(cmd, **_):  # noqa: ANN001, ANN003, ANN202
        seen.append(cmd)
        import subprocess  # noqa: PLC0415

        return subprocess.CompletedProcess(cmd, 0, '{"ok": true, "columns": {}}\n', "")

    monkeypatch.setattr(compose, "_run", run)
    monkeypatch.setattr(compose.shutil, "which", lambda name: name)
    cfg = SimpleNamespace(install_path=tmp_path, compose_file="docker-compose.yaml", profiles=["mcp"])
    ops = rot.ComposeOperations(cfg)
    job = rot.SCOPES[0].jobs[1]
    assert ops.run_job(job, skip_unreadable=True).ok
    command = seen[0]
    tail = command[command.index("run"):]
    assert tail == [
        "run", "--rm", "--no-deps", "-T", "faden-gateway",
        "python", "-m", "faden_gateway.tools.rotate_keys", "--scope", "settings", "--skip-unreadable",
    ]


# ── the command ───────────────────────────────────────────────────


def _cfg(tmp_path: Path, profiles: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        install_dir=str(tmp_path), install_path=tmp_path, env_path=tmp_path / ".env",
        profiles=profiles, compose_file="docker-compose.yaml",
    )


def test_status_names_the_published_keys_and_never_prints_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, **{MASTER: PUBLISHED_DATA_KEYS[MASTER], ALLOW_PUBLISHED_KEYS: "true"})
    monkeypatch.setattr("faden_cli.commands.secrets.load_config", lambda: _cfg(tmp_path, ["mcp"]))
    result = CliRunner().invoke(secrets_group, ["status"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "published default" in flat and "own random value" in flat
    assert "faden secrets rotate" in flat
    for secret in [PUBLISHED_DATA_KEYS[MASTER], OWN_MCP, OWN_CHAT, "the-pepper-dddddddddddd"]:
        assert secret not in result.output


def test_status_says_when_a_rotation_is_unfinished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS="old-master-jjjjjjjjjjjj")
    monkeypatch.setattr("faden_cli.commands.secrets.load_config", lambda: _cfg(tmp_path, []))
    result = CliRunner().invoke(secrets_group, ["status"])
    flat = " ".join(result.output.split())
    assert "rotation unfinished" in flat and "--resume" in flat
    assert "old-master-jjjjjjjjjjjj" not in result.output


def test_the_command_runs_the_rotation_and_reports_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _install(tmp_path)
    monkeypatch.setattr("faden_cli.commands.secrets.load_config", lambda: _cfg(tmp_path, ["mcp"]))
    monkeypatch.setattr(rot, "ComposeOperations", lambda cfg: FakeInstall(cfg.env_path))
    result = CliRunner().invoke(secrets_group, ["rotate", "--only", "settings", "-y"])
    assert result.exit_code == 0, result.output
    assert "re-sealed" in result.output
    assert read_env_file(env)[MASTER] != OWN_MASTER
    assert OWN_MASTER not in result.output


def test_the_command_exits_1_and_says_how_to_continue_when_a_job_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path)
    monkeypatch.setattr("faden_cli.commands.secrets.load_config", lambda: _cfg(tmp_path, []))
    monkeypatch.setattr(rot, "ComposeOperations", lambda cfg: FakeInstall(cfg.env_path, fail_job="backend database"))
    result = CliRunner().invoke(secrets_group, ["rotate", "-y"])
    assert result.exit_code == 1
    assert "--resume" in " ".join(result.output.split())
