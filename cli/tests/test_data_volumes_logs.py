"""Chat attachments live on a volume, and Docker's logs rotate.

The gateway kept chat attachments in its container layer (``/data/faden/chat-files``, no mount): every upgrade
and restore replaced the container and lost them. No service set ``logging:``, so the json-file logs grew
until the disk was full. Both are settings in ``.env`` (``FADEN_DATA_DIR``, ``FADEN_LOG_MAX_SIZE``,
``FADEN_LOG_MAX_FILE``), written by deploy and added by upgrade.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
from pathlib import Path

import click
import pytest

from faden_cli.commands import upgrade
from faden_cli.commands.deploy import set_deploy_options
from faden_cli.core import backup, compose
from faden_cli.core.backup import keep_chat_files as real_keep_chat_files  # before the conftest replaces it
from faden_cli.core.compose import ComposeContext
from faden_cli.core.env_gen import DEPLOY_SETTINGS, default_env_vars, fill_required_env, read_env_file

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"


def test_deploy_writes_the_defaults() -> None:
    env = default_env_vars()
    assert env["FADEN_LOG_MAX_SIZE"] == "50m"
    assert env["FADEN_LOG_MAX_FILE"] == "5"
    assert env["FADEN_DATA_DIR"] == ""


def test_upgrade_adds_them_to_an_older_env_and_keeps_the_operators(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FADEN_LOG_MAX_SIZE=10m\nFADEN_DATA_DIR=\n", encoding="utf-8")
    fill_required_env(env)
    values = read_env_file(env)
    assert values["FADEN_LOG_MAX_SIZE"] == "10m"  # the operator's value stays
    assert values["FADEN_LOG_MAX_FILE"] == "5"  # added
    assert values["FADEN_DATA_DIR"] == ""
    assert set(DEPLOY_SETTINGS) <= set(values)


def test_deploy_options_set_the_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FADEN_LOG_MAX_SIZE=50m\n", encoding="utf-8")
    set_deploy_options(env, data_dir="/srv/fadenstack", log_max_size="100M", log_max_file=3)
    values = read_env_file(env)
    assert values["FADEN_DATA_DIR"] == "/srv/fadenstack"
    assert values["FADEN_LOG_MAX_SIZE"] == "100m"
    assert values["FADEN_LOG_MAX_FILE"] == "3"
    set_deploy_options(env, data_dir=None, log_max_size=None, log_max_file=None)  # nothing given

    assert read_env_file(env) == values


@pytest.mark.parametrize(
    "kwargs",
    [{"data_dir": "C:\\data"}, {"data_dir": "relative"}, {"log_max_size": "big"}, {"log_max_size": "0m"}],
)
def test_deploy_options_refuse_nonsense(tmp_path: Path, kwargs: dict[str, str]) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    args = {"data_dir": None, "log_max_size": None, "log_max_file": None, **kwargs}
    with pytest.raises(click.BadParameter):
        set_deploy_options(env, **args)  # type: ignore[arg-type]


def test_every_service_rotates_its_log_and_the_gateway_mounts_the_chat_files() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    services = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    blocks = re.split(r"\n  (?=[a-z][a-z-]*:\n)", services)
    unlogged = [
        block.split(":", 1)[0]
        for block in blocks
        if "container_name:" in block and "logging: *faden-logging" not in block
    ]
    assert unlogged == []
    assert "max-size: ${FADEN_LOG_MAX_SIZE:-50m}" in text
    assert 'max-file: "${FADEN_LOG_MAX_FILE:-5}"' in text
    assert "${FADEN_DATA_DIR:-faden_chat_files}${FADEN_DATA_DIR:+/chat-files}:/data/faden/chat-files" in text
    assert "\n  faden_chat_files:\n" in text


def test_the_backup_takes_the_chat_files() -> None:
    assert "faden_chat_files" in backup.BACKUP_VOLUMES
    assert backup.DATA_DIR_VOLUMES["faden_chat_files"] == "chat-files"


def _lines(files: dict[str, bytes]) -> bytes:
    """What the helper container prints: SHA-256, size and path of each file."""
    rows = (f"{hashlib.sha256(data).hexdigest()}\t{len(data)}\t{name}\n" for name, data in files.items())
    return "".join(rows).encode()


class FakeDocker:
    """The Docker calls of a backup and of the chat files carry-over, for compose project ``fadenstack``.

    The old container holds ``source``, the volume ``dest``; the helper containers answer as the real ones do
    (hash lines, a copy that skips what exists), with faults to inject.
    """

    def __init__(self) -> None:
        self.container = True
        self.mounts = "sha256:oldgateway /srv/faden/models\n"
        self.volumes: set[str] = set()
        self.source: dict[str, bytes] = {"a.txt": b"one", "sub/my file.txt": b"two two"}
        self.dest: dict[str, bytes] = {}
        self.cp_rc = 0
        self.cp_stderr = ""
        self.copy_rc = 0
        self.drop_after_copy: set[str] = set()
        self.corrupt_after_copy: set[str] = set()
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        """One ``docker`` call."""
        self.calls.append(list(cmd))
        out, err, rc = b"", b"", 0
        if cmd[1] == "inspect":
            if cmd[-1] == "faden-gateway":
                out = self.mounts.encode()
                if not self.container:
                    out, err, rc = b"", b"Error: No such object: faden-gateway", 1
            else:
                out = b"fadenstack"
        elif cmd[1:3] == ["volume", "ls"]:
            labels = dict(a.removeprefix("label=").split("=", 1) for a in cmd if a.startswith("label="))
            name = f"{labels['com.docker.compose.project']}_{labels['com.docker.compose.volume']}"
            out = name.encode() if name in self.volumes else b""
        elif cmd[1:3] == ["volume", "create"]:
            self.volumes.add(cmd[-1])
        elif cmd[1] == "run" and "--to-command" in cmd[-1]:
            out = _lines(self.source)
        elif cmd[1] == "run" and cmd[-1].startswith("cd /dst && find"):
            assert any(a.endswith(":/dst:ro") for a in cmd)  # the volume is only read
            out = _lines(self.dest)
        elif cmd[1] == "run" and "--skip-old-files" in cmd[-1]:
            rc = self.copy_rc
            for name, data in self.source.items():
                self.dest.setdefault(name, data)  # never replaces what is there
            for name in self.drop_after_copy:
                self.dest.pop(name, None)
            for name in self.corrupt_after_copy:
                self.dest[name] = b"damaged"
            err = b"no space left on device" if rc else b""
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)

    def runs(self) -> list[list[str]]:
        """The ``docker run`` calls made."""
        return [c for c in self.calls if c[1] == "run"]

    def copies(self) -> list[list[str]]:
        """The ``docker run`` calls that wrote into a volume."""
        return [c for c in self.runs() if "--skip-old-files" in c[-1]]


class FakePopen:
    """``docker cp <container>:<dir>/. -``: a tar stream on stdout."""

    stdout = None

    def __init__(self, fake: FakeDocker, cmd: list[str]) -> None:
        self.cmd = cmd
        self.fake = fake
        self.stderr = io.BytesIO(fake.cp_stderr.encode())

    def wait(self) -> int:
        """The reader finished (well, or as the fake says)."""
        return self.fake.cp_rc


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", fake)
    monkeypatch.setattr(backup.subprocess, "Popen", lambda cmd, **_: FakePopen(fake, cmd))
    return fake


def test_the_old_containers_files_go_into_the_new_volume_and_are_checked(docker: FakeDocker) -> None:
    result = backup.carry_over_chat_files()
    assert (result.status, result.files, result.size) == ("copied", 2, 10)
    create = next(c for c in docker.calls if c[1:3] == ["volume", "create"])
    assert "com.docker.compose.volume=faden_chat_files" in create  # labelled as compose does, so it adopts it
    assert create[-1] == "fadenstack_faden_chat_files"
    (copy,) = docker.copies()
    assert "fadenstack_faden_chat_files:/dst" in copy
    # The old gateway's own image does it all: on the server already, so an air-gapped one pulls nothing.
    assert all("sha256:oldgateway" in run and "alpine" not in run for run in docker.runs())
    assert docker.dest == docker.source


def test_an_existing_empty_volume_is_used_without_creating_one(docker: FakeDocker) -> None:
    docker.volumes.add("fadenstack_faden_chat_files")
    assert backup.carry_over_chat_files().status == "copied"
    assert not [c for c in docker.calls if c[1:3] == ["volume", "create"]]


def test_a_volume_with_the_same_files_is_no_obstacle(docker: FakeDocker) -> None:
    docker.dest = {"a.txt": b"one", "other.txt": b"already there"}
    assert backup.carry_over_chat_files().status == "copied"
    assert docker.dest["other.txt"] == b"already there" and docker.dest["sub/my file.txt"] == b"two two"


def test_a_volume_with_other_files_gets_the_missing_ones(docker: FakeDocker) -> None:
    docker.dest = {"written-since.txt": b"x"}
    assert backup.carry_over_chat_files().files == 2
    assert docker.dest["written-since.txt"] == b"x" and "a.txt" in docker.dest


def test_a_file_with_other_content_in_the_volume_stops_before_anything_is_written(docker: FakeDocker) -> None:
    docker.dest = {"a.txt": b"someone else's"}
    with pytest.raises(backup.ChatFilesNotCarried, match=r"a\.txt") as stop:
        backup.carry_over_chat_files()
    assert "overwritten" in str(stop.value)
    assert not docker.copies()
    assert docker.dest == {"a.txt": b"someone else's"}


def test_a_file_missing_after_the_copy_stops(docker: FakeDocker) -> None:
    docker.drop_after_copy = {"sub/my file.txt"}
    with pytest.raises(backup.ChatFilesNotCarried, match="not in the data volume after copying: sub/my"):
        backup.carry_over_chat_files()


def test_a_hash_that_differs_after_the_copy_stops(docker: FakeDocker) -> None:
    docker.corrupt_after_copy = {"a.txt"}
    with pytest.raises(backup.ChatFilesNotCarried, match=r"differ .* after copying: a\.txt"):
        backup.carry_over_chat_files()


def test_a_failed_copy_into_the_volume_stops_and_says_what_to_do(docker: FakeDocker) -> None:
    docker.copy_rc = 1
    with pytest.raises(backup.ChatFilesNotCarried, match="no space left on device") as stop:
        backup.carry_over_chat_files()
    assert "still run" in str(stop.value) and "run the command again" in str(stop.value)


def test_a_docker_cp_error_is_a_failure_not_nothing_to_carry(docker: FakeDocker) -> None:
    docker.cp_rc, docker.cp_stderr = 1, "Error response from daemon: i/o error reading layer"
    with pytest.raises(backup.ChatFilesNotCarried, match="i/o error reading layer"):
        backup.carry_over_chat_files()
    assert not docker.copies()


def test_a_container_without_the_directory_has_nothing_to_carry(docker: FakeDocker) -> None:
    docker.cp_rc = 1
    docker.cp_stderr = "Error response from daemon: Could not find the file /data/faden/chat-files in x"
    assert backup.carry_over_chat_files().status == "none"
    assert not docker.copies()


def test_an_empty_directory_has_nothing_to_carry(docker: FakeDocker) -> None:
    docker.source = {}
    assert backup.carry_over_chat_files().status == "none"
    assert not docker.copies() and not [c for c in docker.calls if c[1:3] == ["volume", "create"]]


def test_a_container_that_already_has_the_mount_is_not_copied(docker: FakeDocker) -> None:
    docker.mounts = "sha256:oldgateway /data/faden/chat-files\n"
    assert backup.carry_over_chat_files().status == "present"
    assert not docker.runs()


def test_without_an_old_container_nothing_is_copied(docker: FakeDocker) -> None:
    docker.container = False
    assert backup.carry_over_chat_files().status == "none"
    assert not docker.runs()


def test_with_a_data_dir_the_files_go_to_that_directory(docker: FakeDocker) -> None:
    assert backup.carry_over_chat_files("/srv/fadenstack").status == "copied"
    (copy,) = docker.copies()
    assert "/srv/fadenstack/chat-files:/dst" in copy
    assert not [c for c in docker.calls if c[1:3] == ["volume", "create"]]


def test_keep_chat_files_stops_the_command_with_the_reason(
    docker: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    docker.copy_rc = 1
    with pytest.raises(SystemExit) as stop:
        real_keep_chat_files(None)
    assert stop.value.code == 1
    assert "no space left on device" in capsys.readouterr().out


def test_the_upgrade_stops_before_compose_replaces_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(compose, "_run", run)
    monkeypatch.setattr(compose.shutil, "which", lambda name: name)
    synced: list[object] = []
    monkeypatch.setattr("faden_cli.commands.deploy._sync_postgres_password", lambda *a: synced.append(a))
    monkeypatch.setattr(backup, "keep_chat_files", real_keep_chat_files)  # the conftest's no-op replaced

    def refuse(_data_dir: str = "") -> None:
        raise backup.ChatFilesNotCarried("Chat attachments could not be copied. Free disk space.")

    monkeypatch.setattr(backup, "carry_over_chat_files", refuse)
    ctx = ComposeContext(compose_files=[tmp_path / "docker-compose.yaml"])
    with pytest.raises(SystemExit) as stop:
        upgrade._restart_and_check(ctx, tmp_path, tmp_path / ".env", 4)
    assert stop.value.code == 1
    assert ran == [] and synced == []  # no compose call at all: the old containers run on


def test_the_backup_reads_the_data_dir_when_one_is_set(docker: FakeDocker, tmp_path: Path) -> None:
    created = backup.snapshot_volumes(tmp_path, volumes=["faden_chat_files"], data_dir="/srv/fadenstack")
    assert created == ["faden_chat_files.tar.gz"]
    (run,) = docker.runs()
    assert "/srv/fadenstack/chat-files:/data:ro" in run


def test_the_backup_reads_the_named_volume_otherwise(docker: FakeDocker, tmp_path: Path) -> None:
    docker.volumes.add("fadenstack_faden_chat_files")
    assert backup.snapshot_volumes(tmp_path, volumes=["faden_chat_files"]) == ["faden_chat_files.tar.gz"]
    (run,) = docker.runs()
    assert "fadenstack_faden_chat_files:/data:ro" in run


def test_a_restore_writes_into_the_data_dir_when_one_is_set(docker: FakeDocker, tmp_path: Path) -> None:
    (tmp_path / "faden_chat_files.tar.gz").write_bytes(b"x")
    restored = backup.restore_volumes(
        tmp_path, {"volumes": ["faden_chat_files.tar.gz"]}, data_dir="/srv/fadenstack"
    )
    assert restored == ["faden_chat_files"]
    (run,) = docker.runs()
    assert "/srv/fadenstack/chat-files:/data" in run


def test_data_dir_of_reads_the_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FADEN_DATA_DIR=/srv/fadenstack/\n", encoding="utf-8")
    assert backup.data_dir_of(env) == "/srv/fadenstack"
    env.write_text("FADEN_DATA_DIR=\n", encoding="utf-8")
    assert backup.data_dir_of(env) == ""
    assert backup.data_dir_of(None) == ""
