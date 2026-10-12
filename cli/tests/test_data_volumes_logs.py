"""Chat attachments live on a volume, and Docker's logs rotate.

The gateway kept chat attachments in its container layer (``/data/faden/chat-files``, no mount): every upgrade
and restore replaced the container and lost them. No service set ``logging:``, so the json-file logs grew
until the disk was full. Both are settings in ``.env`` (``FADEN_DATA_DIR``, ``FADEN_LOG_MAX_SIZE``,
``FADEN_LOG_MAX_FILE``), written by deploy and added by upgrade.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click
import pytest

from faden_cli.commands.deploy import set_deploy_options
from faden_cli.core import backup
from faden_cli.core.env_gen import DEPLOY_SETTINGS, default_env_vars, fill_required_env, read_env_file

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"


def test_deploy_writes_the_defaults() -> None:
    env = default_env_vars(admin_password="x")  # noqa: S106
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


class FakeDocker:
    """The Docker calls of a backup and of the chat files carry-over, for compose project ``fadenstack``."""

    def __init__(self) -> None:
        self.container = True
        self.mounts = "/srv/faden/models\n"
        self.volumes: set[str] = set()
        self.volume_full = False
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        """One ``docker`` call."""
        self.calls.append(list(cmd))
        out, rc = "", 0
        if cmd[1] == "inspect":
            if cmd[-1] == "faden-gateway":
                out, rc = self.mounts, 0 if self.container else 1
            else:
                out = "fadenstack"
        elif cmd[1:3] == ["volume", "ls"]:
            labels = dict(a.removeprefix("label=").split("=", 1) for a in cmd if a.startswith("label="))
            name = f"{labels['com.docker.compose.project']}_{labels['com.docker.compose.volume']}"
            out = name if name in self.volumes else ""
        elif cmd[1:3] == ["volume", "create"]:
            self.volumes.add(cmd[-1])
        elif cmd[1] == "run" and "-i" in cmd:
            rc = 3 if self.volume_full else 0
        return subprocess.CompletedProcess(cmd, rc, stdout=out.encode(), stderr=b"")

    def runs(self) -> list[list[str]]:
        """The ``docker run`` calls made."""
        return [c for c in self.calls if c[1] == "run"]


class FakePopen:
    """``docker cp <container>:<dir>/. -``: a tar stream on stdout."""

    stdout = None

    def __init__(self, cmd: list[str], **_: object) -> None:
        self.cmd = cmd

    def wait(self) -> int:
        """The reader finished well."""
        return 0


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", fake)
    monkeypatch.setattr(backup.subprocess, "Popen", FakePopen)
    return fake


def test_the_old_containers_files_go_into_the_new_volume(docker: FakeDocker) -> None:
    assert backup.carry_over_chat_files() == "copied"
    create = next(c for c in docker.calls if c[1:3] == ["volume", "create"])
    assert "com.docker.compose.volume=faden_chat_files" in create  # labelled as compose does, so it adopts it
    assert create[-1] == "fadenstack_faden_chat_files"
    (run,) = docker.runs()
    assert "fadenstack_faden_chat_files:/dst" in run
    assert "ls -A /dst" in run[-1]  # written only into an empty volume


def test_an_existing_empty_volume_is_used_without_creating_one(docker: FakeDocker) -> None:
    docker.volumes.add("fadenstack_faden_chat_files")
    assert backup.carry_over_chat_files() == "copied"
    assert not [c for c in docker.calls if c[1:3] == ["volume", "create"]]


def test_a_volume_with_files_is_left_alone(docker: FakeDocker) -> None:
    docker.volume_full = True
    assert backup.carry_over_chat_files() == "present"


def test_a_container_that_already_has_the_mount_is_not_copied(docker: FakeDocker) -> None:
    docker.mounts = "/data/faden/chat-files\n"
    assert backup.carry_over_chat_files() == "present"
    assert not docker.runs()


def test_without_an_old_container_nothing_is_copied(docker: FakeDocker) -> None:
    docker.container = False
    assert backup.carry_over_chat_files() == "none"
    assert not docker.runs()


def test_with_a_data_dir_the_files_go_to_that_directory(docker: FakeDocker) -> None:
    assert backup.carry_over_chat_files("/srv/fadenstack") == "copied"
    (run,) = docker.runs()
    assert "/srv/fadenstack/chat-files:/dst" in run
    assert not [c for c in docker.calls if c[1:3] == ["volume", "create"]]


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
