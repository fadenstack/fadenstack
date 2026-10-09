"""``.env``, the backups and the saved credentials are readable by the user who runs ``faden`` only.

``.env`` was written 0644 (0664 on a live install) and backups were 0775 directories with 0664 dumps, manifest
and ``.env.bak``, and root-owned 0644 volume archives: every local user could read every password, key and
database of the install. Compose reads ``.env`` as the same user, and no container mounts it or the backups.
"""

# ruff: noqa: PLR2004 - file modes are the point
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from faden_cli.core import backup, bootstrap, private_files
from faden_cli.core.compose import ComposeContext
from faden_cli.core.env_gen import read_env_file, set_module_env, write_env_file

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows has no such mode bits")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── .env ──────────────────────────────────────────────────────────


@posix_only
def test_a_new_env_is_its_owners_only(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_file(env, {"POSTGRES_PASSWORD": "pw"})
    assert _mode(env) == 0o600
    assert read_env_file(env) == {"POSTGRES_PASSWORD": "pw"}


@posix_only
def test_rewriting_an_env_others_could_read_makes_it_private(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=old\n", encoding="utf-8")
    env.chmod(0o664)

    write_env_file(env, {"POSTGRES_PASSWORD": "new"})
    assert _mode(env) == 0o600
    assert read_env_file(env)["POSTGRES_PASSWORD"] == "new"

    env.chmod(0o644)
    set_module_env(env, enable=["mcp"])
    assert _mode(env) == 0o600


def test_written_files_hold_exactly_what_was_written(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"a much longer earlier content")
    private_files.write_private(target, b"line\n")
    assert target.read_bytes() == b"line\n", "truncated, and no CRLF on Windows"


@posix_only
def test_the_saved_credentials_are_never_readable_by_others(tmp_path: Path) -> None:
    saved = bootstrap.save_credentials_file({"email": "admin@localhost", "password": "pw"}, tmp_path)
    assert saved is not None
    assert _mode(saved) == 0o600
    assert "ADMIN_PASSWORD=pw" in saved.read_text(encoding="utf-8")


# ── an install made before ────────────────────────────────────────


def _old_install(tmp_path: Path) -> Path:
    install = tmp_path / "fadenstack"
    old = install / "backups" / "20261001T030000Z"
    old.mkdir(parents=True)
    (install / ".env").write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
    (install / ".bootstrap-credentials").write_text("ADMIN_PASSWORD=pw\n", encoding="utf-8")
    for name in ("faden_backend.dump", "manifest.json", ".env.bak", "minio_data.tar.gz"):
        (old / name).write_bytes(b"x")
    (install / "backups" / "backup.log").write_text("ran\n", encoding="utf-8")
    if os.name != "nt":
        (install / ".env").chmod(0o664)
        (install / ".bootstrap-credentials").chmod(0o644)
        for path in [install / "backups", old]:
            path.chmod(0o775)
        for path in old.iterdir():
            path.chmod(0o664)
    return install


@posix_only
def test_deploy_and_upgrade_make_an_older_installs_secrets_private(tmp_path: Path) -> None:
    install = _old_install(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.write_text("not ours", encoding="utf-8")
    outside.chmod(0o644)
    (install / "backups" / "link").symlink_to(outside)

    assert private_files.secure_install(install) == []

    assert _mode(install / ".env") == 0o600
    assert _mode(install / ".bootstrap-credentials") == 0o600
    for path in (install / "backups").rglob("*"):
        if path.is_symlink():
            continue
        assert _mode(path) == (0o700 if path.is_dir() else 0o600), path
    assert _mode(install / "backups") == 0o700
    assert _mode(outside) == 0o644, "a link is not followed"


@posix_only
def test_archives_root_wrote_stay_but_out_of_reach_and_an_open_env_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    install = _old_install(tmp_path)
    archive = install / "backups" / "20261001T030000Z" / "minio_data.tar.gz"
    real_chmod = Path.chmod

    def chmod(self: Path, mode: int, **kwargs: Any) -> None:
        if self in (archive, install / ".env"):  # another user's: root's archive, a shared .env
            raise PermissionError(1, "Operation not permitted", str(self))
        real_chmod(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", chmod)

    exposed = private_files.secure_install(install)

    assert exposed == [install / ".env"], "the archive sits in a directory nobody else can enter"
    assert _mode(install / "backups") == 0o700
    out = " ".join(capsys.readouterr().out.split())
    assert "could not be made readable by their owner only" in out
    assert "nobody else reaches them" in out


def test_securing_an_install_without_backups_or_credentials_is_fine(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=b\n", encoding="utf-8")
    assert private_files.secure_install(tmp_path) == []


# ── backups ───────────────────────────────────────────────────────


class FakeDocker:
    """The Docker calls of a backup and a restore, for an install whose compose project is ``fadenstack``."""

    def __init__(self) -> None:
        self.runs: list[list[str]] = []
        self.stdin: list[bytes] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        """One ``docker`` call; a ``docker run`` writes to its output file and reads its input."""
        text = kwargs.get("text", False)
        out = ""
        if cmd[1] == "inspect":
            out = "fadenstack"
        elif cmd[1:3] == ["volume", "ls"]:
            labels = dict(arg.removeprefix("label=").split("=", 1) for arg in cmd if arg.startswith("label="))
            out = f"fadenstack_{labels['com.docker.compose.volume']}"
        elif cmd[1] == "exec" and "psql" in cmd:
            out = "faden_backend\nfaden_gateway\n"
        elif cmd[1] == "exec" and "pg_dump" in cmd:
            out = f"DUMP {cmd[-1]}"
        elif cmd[1] == "compose" and "alembic" in cmd:
            out = "abc123 (head)"
        elif cmd[1] == "run":
            self.runs.append(cmd)
            if kwargs.get("stdout") not in (None, subprocess.PIPE):
                kwargs["stdout"].write(b"TARBALL")
            if kwargs.get("stdin") is not None:
                self.stdin.append(kwargs["stdin"].read())
        if text:
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=out.encode(), stderr=b"")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", fake)
    monkeypatch.setattr(backup, "compose_up", lambda *_a, **_k: 0)
    return fake


def test_a_backup_is_its_owners_only_and_the_volume_archives_too(docker: FakeDocker, tmp_path: Path) -> None:
    install = _old_install(tmp_path)
    ctx = ComposeContext(compose_files=[install / "docker-compose.yaml"], env_file=install / ".env",
                         project_dir=install)

    result = backup.create_backup(ctx, output_dir=install / "backups", include_volumes=True)

    assert result.ok, result.errors
    made = result.backup_dir
    assert (made / ".env.bak").read_bytes() == (install / ".env").read_bytes()
    assert (made / "faden_backend.dump").read_bytes() == b"DUMP faden_backend"
    assert (made / "minio_data.tar.gz").read_bytes() == b"TARBALL", "the CLI writes the container's output"
    # The helper container mounts the volume only: it writes no file of its own into the backup.
    tar_runs = [run for run in docker.runs if "tar" in run]
    assert tar_runs
    assert all(run.count("-v") == 1 and "czf" in run and "-" in run for run in tar_runs)
    if os.name != "nt":
        assert _mode(install / "backups") == 0o700, "the install's own backups directory is tightened"
        assert _mode(made) == 0o700
        for path in made.rglob("*"):
            assert _mode(path) == (0o700 if path.is_dir() else 0o600), path


@posix_only
def test_a_backup_into_the_operators_directory_leaves_its_mode(docker: FakeDocker, tmp_path: Path) -> None:
    install = _old_install(tmp_path)
    disk = tmp_path / "backup-disk"
    disk.mkdir()
    disk.chmod(0o755)
    ctx = ComposeContext(env_file=install / ".env", project_dir=install)

    result = backup.create_backup(ctx, output_dir=disk)

    assert result.ok, result.errors
    assert _mode(disk) == 0o755
    assert _mode(result.backup_dir) == 0o700
    assert all(_mode(path) == 0o600 for path in result.backup_dir.iterdir() if path.is_file())


def test_a_restore_hands_the_archive_to_the_container_on_its_input(
    docker: FakeDocker, tmp_path: Path,
) -> None:
    (tmp_path / "minio_data.tar.gz").write_bytes(b"TARBALL")
    if os.name != "nt":
        (tmp_path / "minio_data.tar.gz").chmod(0o600)

    assert backup.restore_volumes(tmp_path, {"volumes": ["minio_data.tar.gz"]}) == ["minio_data"]

    (run,) = docker.runs
    assert "-i" in run
    assert run.count("-v") == 1, "the backup is not mounted: a container's root may not read it"
    assert run[-1].endswith("tar xzf - -C /data")
    assert docker.stdin == [b"TARBALL"]


@posix_only
def test_an_env_restored_from_a_backup_is_private(tmp_path: Path) -> None:
    (tmp_path / ".env.bak").write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=other\n", encoding="utf-8")
    env.chmod(0o664)

    assert backup.restore_env(tmp_path, env)
    assert _mode(env) == 0o600
    assert read_env_file(env)["POSTGRES_PASSWORD"] == "pw"
