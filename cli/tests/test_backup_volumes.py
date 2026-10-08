"""`--include-volumes` backs up the install's own volumes, and a restore writes them back.

Compose names each volume after its project (``fadenstack_minio_data``). The backup asked Docker for the bare
compose name instead, so it tarred a new, empty volume, and a restore filled that one: the knowledge-base
uploads were never in a backup, and Postgres's data volume would have been written under the running database.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from faden_cli.core import backup

INSTALLED = {"fadenstack_faden_rag_lite", "fadenstack_minio_data", "fadenstack_pg_data"}


class FakeDocker:
    """Answers the Docker calls a backup makes, for an install whose compose project is ``fadenstack``."""

    def __init__(self) -> None:
        self.mounts: list[str] = []
        self.scripts: list[str] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        """One ``docker`` call: the project label, a volume lookup or a ``docker run``."""
        out = ""
        if cmd[1] == "inspect":
            out = "fadenstack"
        elif cmd[1:3] == ["volume", "ls"]:
            labels = dict(arg.removeprefix("label=").split("=", 1) for arg in cmd if arg.startswith("label="))
            name = f"{labels['com.docker.compose.project']}_{labels['com.docker.compose.volume']}"
            out = name if name in INSTALLED else ""
        elif cmd[1] == "run":
            self.mounts.append(cmd[cmd.index("-v") + 1])
            self.scripts.append(" ".join(cmd[cmd.index("alpine") + 1 :]))
        return subprocess.CompletedProcess(cmd, 0, stdout=out.encode(), stderr=b"")


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", fake)
    return fake


def test_the_backup_reads_the_installs_own_volumes(docker: FakeDocker, tmp_path: Path) -> None:
    created = backup.snapshot_volumes(tmp_path)

    assert created == ["faden_rag_lite.tar.gz", "minio_data.tar.gz"]
    assert docker.mounts == ["fadenstack_faden_rag_lite:/data:ro", "fadenstack_minio_data:/data:ro"]


def test_neither_postgres_data_nor_the_models_are_in_the_volume_backup() -> None:
    assert "pg_data" not in backup.BACKUP_VOLUMES
    assert "faden_models" not in backup.BACKUP_VOLUMES
    assert "faden_rag_lite" in backup.BACKUP_VOLUMES


def test_the_restore_writes_into_the_installs_volumes_not_postgres(
    docker: FakeDocker, tmp_path: Path
) -> None:
    archives = ["pg_data.tar.gz", "faden_rag_lite.tar.gz", "minio_data.tar.gz", "loki_data.tar.gz"]
    for name in archives:
        (tmp_path / name).write_bytes(b"")

    restored = backup.restore_volumes(tmp_path, {"volumes": archives})

    # pg_data is an older backup's: the dumps restore the databases. loki_data is not in this install.
    assert restored == ["faden_rag_lite", "minio_data"]
    assert docker.mounts == ["fadenstack_faden_rag_lite:/data", "fadenstack_minio_data:/data"]
    assert all(script.startswith("sh -c find /data -mindepth 1 -delete && ") for script in docker.scripts)


def test_nothing_is_touched_without_an_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def no_install(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"No such object: faden-postgres")

    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", no_install)

    assert backup.snapshot_volumes(tmp_path) == []
    assert not [cmd for cmd in calls if cmd[1] == "run"]
