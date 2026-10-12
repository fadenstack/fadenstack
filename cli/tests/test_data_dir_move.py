"""Switching FADEN_DATA_DIR on moves the files of the named volumes it covers into its folders.

Without that, the containers about to be created would mount empty folders and the company's chat attachments
and knowledge files would look lost. The copy never overwrites, every file is checked by SHA-256, and any
failure stops the command before compose creates anything.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from faden_cli.commands import up as up_command
from faden_cli.core import backup
from faden_cli.core.backup import keep_chat_files as real_keep_chat_files  # before the conftest replaces them
from faden_cli.core.backup import keep_company_files as real_keep_company_files
from faden_cli.core.compose import ComposeContext

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"
DATA = "/srv/fadenstack"
CHAT, KNOWLEDGE = "fadenstack_faden_chat_files", "fadenstack_faden_rag_lite"
CHAT_FILES = {"a.txt": b"one", "sub/my file.txt": b"two two"}
KNOWLEDGE_FILES = {"handbook.pdf": b"%PDF handbook", "hr/policy.docx": b"policy"}


def _lines(files: dict[str, bytes]) -> bytes:
    rows = (f"{hashlib.sha256(data).hexdigest()}\t{len(data)}\t{name}\n" for name, data in files.items())
    return "".join(rows).encode()


class FakeDocker:
    """The Docker calls of the move: named volumes (``volumes``) and host folders (``folders``) holding files,
    helper containers that hash and copy as the real ones do, with faults to inject."""

    def __init__(self) -> None:
        self.volumes: dict[str, dict[str, bytes]] = {}
        self.folders: dict[str, dict[str, bytes]] = {}
        self.gateway_image: str | None = "sha256:gateway"
        self.backend_image: str | None = "sha256:backend"
        self.copy_rc = 0
        self.drop_after_copy: set[str] = set()
        self.corrupt_after_copy: set[str] = set()
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(cmd))
        out, err, rc = b"", b"", 0
        mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
        if cmd[1] == "inspect" and cmd[-1] == "faden-gateway":
            out, rc = (self.gateway_image or "").encode(), 0 if self.gateway_image else 1
        elif cmd[1] == "inspect" and cmd[-1] == "faden-backend":
            out, rc = (self.backend_image or "").encode(), 0 if self.backend_image else 1
        elif cmd[1] == "inspect":
            out = b"fadenstack"
        elif cmd[1:3] == ["volume", "ls"]:
            volume = next(a.removeprefix("label=com.docker.compose.volume=") for a in cmd if "compose.volume=" in a)
            name = f"fadenstack_{volume}"
            out = name.encode() if name in self.volumes else b""
        elif cmd[1] == "run" and cmd[-1].startswith("cd /src && find"):
            assert mounts[0].endswith(":/src:ro")
            out = _lines(self.volumes[mounts[0].removesuffix(":/src:ro")])
        elif cmd[1] == "run" and cmd[-1].startswith("cd /dst && find"):
            assert mounts[0].endswith(":/dst:ro")  # a folder is only read
            out = _lines(self.folders.get(mounts[0].removesuffix(":/dst:ro"), {}))
        elif cmd[1] == "run" and "--skip-old-files" in cmd[-1]:
            rc = self.copy_rc
            folder = next(m.removesuffix(":/dst") for m in mounts if m.endswith(":/dst"))
            volume = next(m.removesuffix(":/src:ro") for m in mounts if m.endswith(":/src:ro"))
            target = self.folders.setdefault(folder, {})
            for name, data in self.volumes[volume].items():
                target.setdefault(name, data)  # never replaces what is there
            for name in self.drop_after_copy:
                target.pop(name, None)
            for name in self.corrupt_after_copy:
                target[name] = b"damaged"
            err = b"no space left on device" if rc else b""
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)

    def copies(self) -> list[list[str]]:
        return [c for c in self.calls if c[1] == "run" and "--skip-old-files" in c[-1]]

    def runs(self) -> list[list[str]]:
        return [c for c in self.calls if c[1] == "run"]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(backup, "_docker_bin", lambda: "docker")
    monkeypatch.setattr(backup.subprocess, "run", fake)
    return fake


def _env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(f"FADEN_DATA_DIR={DATA}\n", encoding="utf-8")
    return env


def test_the_files_of_the_volume_go_into_the_folder_and_are_checked(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    chat, knowledge = backup.move_into_data_dir(DATA)
    assert (chat.volume, chat.status) == ("faden_chat_files", "none")  # no such volume: nothing to move
    assert (knowledge.status, knowledge.files, knowledge.size) == ("moved", 2, 19)
    assert docker.folders[f"{DATA}/knowledge"] == KNOWLEDGE_FILES
    (copy,) = docker.copies()
    assert f"{KNOWLEDGE}:/src:ro" in copy  # the volume is read, never written
    assert docker.volumes[KNOWLEDGE] == KNOWLEDGE_FILES  # and left as it was


def test_both_volumes_are_handled_by_one_call(docker: FakeDocker) -> None:
    docker.volumes[CHAT] = dict(CHAT_FILES)
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    results = backup.move_into_data_dir(DATA)
    assert [(r.volume, r.status) for r in results] == [("faden_chat_files", "moved"), ("faden_rag_lite", "moved")]
    assert docker.folders[f"{DATA}/chat-files"] == CHAT_FILES
    assert docker.folders[f"{DATA}/knowledge"] == KNOWLEDGE_FILES


def test_the_helper_is_an_image_already_on_the_server(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    backup.move_into_data_dir(DATA)
    assert all("sha256:gateway" in run and "alpine" not in run for run in docker.runs())
    docker.calls.clear()
    docker.folders.clear()
    docker.gateway_image = None  # no gateway container: the backend's image
    backup.move_into_data_dir(DATA)
    assert docker.runs() and all("sha256:backend" in run for run in docker.runs())


def test_without_a_running_container_the_compose_files_backend_image_is_used(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.gateway_image = docker.backend_image = None
    ctx = ComposeContext(compose_files=[Path("docker-compose.yaml")])

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        if cmd[-2:] == ["config", "--images"]:
            return subprocess.CompletedProcess(cmd, 0, "ghcr.io/x/gateway:1\nghcr.io/x/backend:1\n", "")
        return docker(cmd, **kwargs)

    monkeypatch.setattr(backup.subprocess, "run", run)
    backup.move_into_data_dir(DATA, ctx)
    assert docker.runs() and all("ghcr.io/x/backend:1" in r for r in docker.runs())
    with pytest.raises(backup.DataDirNotMoved, match="No image"):
        backup.move_into_data_dir(DATA)  # no compose context either


def test_nothing_to_move_without_a_data_dir_or_without_files(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    assert backup.move_into_data_dir("") == []
    assert not docker.calls  # named volumes: Docker is not even asked
    docker.volumes[KNOWLEDGE] = {}
    assert {r.status for r in backup.move_into_data_dir(DATA)} == {"none"}
    assert not docker.copies()


def test_a_folder_that_holds_the_same_files_is_left_alone_and_a_rerun_is_a_no_op(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.folders[f"{DATA}/knowledge"] = {**KNOWLEDGE_FILES, "added-later.txt": b"x"}
    assert backup.move_into_data_dir(DATA)[1].status == "present"
    assert not docker.copies()
    docker.folders.clear()
    assert backup.move_into_data_dir(DATA)[1].status == "moved"
    assert backup.move_into_data_dir(DATA)[1].status == "present"
    assert len(docker.copies()) == 1


def test_a_folder_with_other_files_gets_the_missing_ones(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.folders[f"{DATA}/knowledge"] = {"handbook.pdf": KNOWLEDGE_FILES["handbook.pdf"], "other.txt": b"mine"}
    assert backup.move_into_data_dir(DATA)[1].status == "moved"
    assert docker.folders[f"{DATA}/knowledge"] == {**KNOWLEDGE_FILES, "other.txt": b"mine"}


def test_a_file_with_other_content_is_a_conflict_that_overwrites_nothing(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.folders[f"{DATA}/knowledge"] = {"hr/policy.docx": b"someone else's"}
    with pytest.raises(backup.DataDirNotMoved, match=r"hr/policy\.docx") as stop:
        backup.move_into_data_dir(DATA)
    assert "overwritten" in str(stop.value)
    assert not docker.copies()
    assert docker.folders[f"{DATA}/knowledge"] == {"hr/policy.docx": b"someone else's"}


def test_a_file_missing_after_the_copy_stops(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.drop_after_copy = {"handbook.pdf"}
    with pytest.raises(backup.DataDirNotMoved, match="not in .* after copying: handbook.pdf"):
        backup.move_into_data_dir(DATA)


def test_a_hash_that_differs_after_the_copy_stops(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.corrupt_after_copy = {"handbook.pdf"}
    with pytest.raises(backup.DataDirNotMoved, match=r"differ in .* after copying: handbook\.pdf"):
        backup.move_into_data_dir(DATA)


def test_a_failed_copy_stops_and_says_what_to_do(docker: FakeDocker) -> None:
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.copy_rc = 1
    with pytest.raises(backup.DataDirNotMoved, match="no space left on device") as stop:
        backup.move_into_data_dir(DATA)
    assert "still run" in str(stop.value) and "run the command again" in str(stop.value)


def test_the_chat_carry_over_runs_first_then_the_move(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Upgrade from 0.4.3 with FADEN_DATA_DIR set: the old gateway's files go straight into the folder (no
    named volume is made for them); the move after it finds no volume for them."""
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    monkeypatch.setattr(backup, "keep_chat_files", real_keep_chat_files)
    order: list[str] = []
    monkeypatch.setattr(
        backup, "carry_over_chat_files", lambda d="": order.append(f"chat:{d}") or backup.CarryResult("none")
    )
    real_move = backup.move_into_data_dir
    monkeypatch.setattr(backup, "move_into_data_dir", lambda d, c=None: order.append(f"move:{d}") or real_move(d, c))
    real_keep_company_files(_env(tmp_path))
    assert order == [f"chat:{DATA}", f"move:{DATA}"]
    assert docker.folders[f"{DATA}/knowledge"] == KNOWLEDGE_FILES
    assert not [c for c in docker.calls if c[1:3] == ["volume", "create"]]
    assert "left as it was" in capsys.readouterr().out.replace("\n", " ")


def test_the_two_steps_do_not_fight_over_the_same_files(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat files an old container carried into the folder next to a named chat volume: the same file is not
    copied twice, and a different one is a conflict that stops the command, never an overwrite."""
    monkeypatch.setattr(backup, "keep_chat_files", real_keep_chat_files)
    monkeypatch.setattr(backup, "carry_over_chat_files", lambda d="": backup.CarryResult("none"))
    docker.volumes[CHAT] = dict(CHAT_FILES)
    docker.folders[f"{DATA}/chat-files"] = {"a.txt": b"one"}  # carried in from the old container
    real_keep_company_files(_env(tmp_path))
    assert docker.folders[f"{DATA}/chat-files"] == CHAT_FILES
    docker.folders[f"{DATA}/chat-files"]["a.txt"] = b"changed"
    with pytest.raises(SystemExit):
        real_keep_company_files(_env(tmp_path))
    assert docker.folders[f"{DATA}/chat-files"]["a.txt"] == b"changed"


def _up_with(ctx: ComposeContext, monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    monkeypatch.setattr(backup, "keep_company_files", real_keep_company_files)
    monkeypatch.setattr(up_command, "load_config", lambda: object())
    monkeypatch.setattr(up_command, "build_context_from_config", lambda _cfg: ctx)
    monkeypatch.setattr(up_command, "compose_up", lambda *_a, **_k: seen.append("compose up") or 0)


def test_faden_up_moves_the_files_before_compose_up(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "carry_over_chat_files", lambda d="": backup.CarryResult("none"))
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    seen: list[str] = []
    ctx = ComposeContext(compose_files=[tmp_path / "docker-compose.yaml"], env_file=_env(tmp_path))
    _up_with(ctx, monkeypatch, seen)
    real_move = backup.move_into_data_dir
    monkeypatch.setattr(backup, "move_into_data_dir", lambda d, c=None: seen.append("move") or real_move(d, c))
    assert CliRunner().invoke(up_command.up_cmd, []).exit_code == 0
    assert seen == ["move", "compose up"]
    assert docker.folders[f"{DATA}/knowledge"] == KNOWLEDGE_FILES


def test_a_failed_move_stops_faden_up_before_compose_runs(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "carry_over_chat_files", lambda d="": backup.CarryResult("none"))
    docker.volumes[KNOWLEDGE] = dict(KNOWLEDGE_FILES)
    docker.copy_rc = 1
    seen: list[str] = []
    ctx = ComposeContext(compose_files=[tmp_path / "docker-compose.yaml"], env_file=_env(tmp_path))
    _up_with(ctx, monkeypatch, seen)
    result = CliRunner().invoke(up_command.up_cmd, [])
    assert result.exit_code == 1
    assert seen == []  # compose was never asked to create anything
    assert "no space left on device" in result.output


# ── Compose ─────────────────────────────────────────────────────────────────


def test_every_service_that_mounts_the_knowledge_files_follows_the_data_dir() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    mount = "${FADEN_DATA_DIR:-faden_rag_lite}${FADEN_DATA_DIR:+/knowledge}:/data/faden/rag-lite"
    assert text.count(mount) == 2  # faden-backend and faden-backend-worker
    assert "- faden_rag_lite:/data/faden/rag-lite" not in text  # no service is left on the named volume
    assert "\n  faden_rag_lite:\n" in text  # the named volume stays for installs without FADEN_DATA_DIR


def test_what_the_data_dir_never_covers() -> None:
    assert set(backup.DATA_DIR_VOLUMES) == {"faden_chat_files", "faden_rag_lite"}
    text = COMPOSE.read_text(encoding="utf-8")
    for never in ("pg_data", "faden_models", "loki_data", "grafana_data", "ch_data"):
        assert f"FADEN_DATA_DIR:-{never}" not in text


# ── Backups and restores ────────────────────────────────────────────────────


def test_the_backup_takes_the_knowledge_folder_when_a_data_dir_is_set(docker: FakeDocker, tmp_path: Path) -> None:
    assert "faden_rag_lite" in backup.BACKUP_VOLUMES
    created = backup.snapshot_volumes(tmp_path, volumes=["faden_rag_lite"], data_dir=DATA)
    assert created == ["faden_rag_lite.tar.gz"]
    (run,) = docker.runs()
    assert f"{DATA}/knowledge:/data:ro" in run


def test_the_backup_takes_the_named_knowledge_volume_otherwise(docker: FakeDocker, tmp_path: Path) -> None:
    docker.volumes[KNOWLEDGE] = {}
    assert backup.snapshot_volumes(tmp_path, volumes=["faden_rag_lite"]) == ["faden_rag_lite.tar.gz"]
    (run,) = docker.runs()
    assert f"{KNOWLEDGE}:/data:ro" in run


def test_a_restore_writes_the_knowledge_folder_when_a_data_dir_is_set(docker: FakeDocker, tmp_path: Path) -> None:
    (tmp_path / "faden_rag_lite.tar.gz").write_bytes(b"x")
    restored = backup.restore_volumes(tmp_path, {"volumes": ["faden_rag_lite.tar.gz"]}, data_dir=DATA)
    assert restored == ["faden_rag_lite"]
    (run,) = docker.runs()
    assert f"{DATA}/knowledge:/data" in run
