"""Encrypted backups and the recovery key (features/admin-guardrails.md, 6.3 and 6.4).

A backup held every database, ``.env`` and the TLS keys in the clear: whoever had the backup disk had
everything. Backups are now encrypted to the install's public key, so the nightly one runs unattended, and
only the recovery key, shown once and kept off the server, opens them. Backups made before stay readable.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from faden_cli.commands import restore as restore_command
from faden_cli.commands.backup import backup_cmd
from faden_cli.core import backup, backup_crypto, backup_key
from faden_cli.core.backup_crypto import OpenError, RecoveryKey
from faden_cli.core.compose import ComposeContext

# ── The key's text ────────────────────────────────────────────────


def test_the_recovery_key_reads_back_however_it_was_copied() -> None:
    key = RecoveryKey.generate()
    prefix, parts = backup_crypto.groups(key.secret)
    assert prefix == "FADEN-BACKUP-KEY1"
    assert [len(part) for part in parts] == [5] * 11 + [3]
    for copied in (
        key.secret, key.secret.lower(), f"{prefix} {' '.join(parts)}", " ".join(parts), "\n".join(parts),
    ):
        assert RecoveryKey.from_text(copied).secret == key.secret


@pytest.mark.parametrize("position", [0, 7, 30, 57])
def test_a_mistyped_character_is_caught_by_the_checksum(position: int) -> None:
    _, parts = backup_crypto.groups(RecoveryKey.generate().secret)
    body = list("".join(parts).lower())
    body[position] = "q" if body[position] != "q" else "p"
    with pytest.raises(backup_crypto.KeyTextError, match="checksum"):
        RecoveryKey.from_text("".join(body))


def test_a_public_key_is_no_recovery_key() -> None:
    key = RecoveryKey.generate()
    with pytest.raises(backup_crypto.KeyTextError):
        RecoveryKey.from_text(key.public)
    assert backup_crypto.key_id(backup_crypto.public_key(key.public)) == key.id


# ── One file ──────────────────────────────────────────────────────


def _sealed(data: bytes, key: RecoveryKey) -> bytes:
    out = io.BytesIO()
    backup_crypto.encrypt_stream(io.BytesIO(data), out, key.private.public_key())
    return out.getvalue()


def _opened(sealed: bytes, key: RecoveryKey) -> bytes:
    out = io.BytesIO()
    backup_crypto.decrypt_stream(io.BytesIO(sealed), out, key)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, 64 * 1024 - 1, 64 * 1024, 64 * 1024 + 1, 3 * 64 * 1024 + 17])
def test_a_file_opens_with_its_key_to_the_same_bytes(size: int) -> None:
    key = RecoveryKey.generate()
    data = os.urandom(size)
    sealed = _sealed(data, key)
    assert size < 32 or data[:32] not in sealed  # noqa: PLR2004
    assert _opened(sealed, key) == data


def test_a_changed_file_does_not_open() -> None:
    key = RecoveryKey.generate()
    sealed = bytearray(_sealed(b"pg_dump of every chat" * 1000, key))
    sealed[len(sealed) // 2] ^= 1
    with pytest.raises(OpenError, match="changed or cut short"):
        _opened(bytes(sealed), key)


def test_a_file_cut_at_a_chunk_boundary_does_not_open() -> None:
    """The last chunk is marked: a file missing its end is not taken for a shorter one."""
    key = RecoveryKey.generate()
    sealed = _sealed(os.urandom(3 * 64 * 1024), key)
    header = len(backup_crypto.MAGIC) + 8 + 32
    with pytest.raises(OpenError):
        _opened(sealed[: header + 2 * (64 * 1024 + 16)], key)


def test_another_key_is_told_apart() -> None:
    sealed = _sealed(b"x", RecoveryKey.generate())
    other = RecoveryKey.generate()
    with pytest.raises(OpenError, match="made for another key"):
        _opened(sealed, other)


# ── A backup ──────────────────────────────────────────────────────


def _backup_folder(root: Path, kind: str = "manual") -> Path:
    folder = root / "20261018T030000Z"
    (folder / "tls-material" / "tls-ca").mkdir(parents=True)
    (folder / "faden_backend.dump").write_bytes(b"DUMP of every user")
    (folder / ".env.bak").write_text("FADEN_GATEWAY_ENCRYPTION_KEY=secret\n", encoding="utf-8")
    (folder / "tls-material" / "tls-ca" / "ca.key").write_text("PRIVATE KEY\n", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"version": 1, "kind": kind}), encoding="utf-8")
    return folder


def test_every_file_of_a_backup_is_encrypted_and_opens_again(tmp_path: Path) -> None:
    key = RecoveryKey.generate()
    folder = _backup_folder(tmp_path)
    before = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    done = backup_crypto.encrypt_backup(folder, key.private.public_key(), kind="nightly")

    assert sorted(done) == sorted(before)
    left = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert left == sorted([*(name + ".fenc" for name in before), "backup.json"])
    for path in folder.rglob("*.fenc"):
        assert b"secret" not in path.read_bytes() and b"PRIVATE KEY" not in path.read_bytes()
    index = backup_crypto.read_index(folder)
    assert index is not None and (index["kind"], index["key_id"], index["files"]) == ("nightly", key.id, 4)

    opened = backup_crypto.open_backup(folder, key)
    after = {p.relative_to(opened).as_posix(): p.read_bytes() for p in opened.rglob("*") if p.is_file()}
    assert after == before
    if os.name != "nt":
        assert stat.S_IMODE(opened.stat().st_mode) == 0o700
    backup_crypto.close_backup(folder)
    assert not opened.exists()


def test_the_wrong_recovery_key_is_named_before_anything_is_written(tmp_path: Path) -> None:
    folder = _backup_folder(tmp_path)
    backup_crypto.encrypt_backup(folder, RecoveryKey.generate().private.public_key(), kind="manual")
    with pytest.raises(OpenError, match="encrypted to the key"):
        backup_crypto.open_backup(folder, RecoveryKey.generate())
    assert not (folder / backup_crypto.OPENED).exists()


def test_encrypted_backups_keep_their_kind_for_retention(tmp_path: Path) -> None:
    key = RecoveryKey.generate()
    for day in range(1, 5):
        folder = _backup_folder(tmp_path / f"d{day}", "nightly")
        target = tmp_path / f"2026100{day}T030000Z"
        folder.rename(target)
        backup_crypto.encrypt_backup(target, key.private.public_key(), kind="nightly")
    assert backup.rotate_backups(tmp_path, retain=2, kind="manual") == 0
    assert backup.rotate_backups(tmp_path, retain=2, kind="nightly") == 2
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("2026")) == [
        "20261003T030000Z", "20261004T030000Z",
    ]


def test_a_changed_env_snapshot_fails_the_check(tmp_path: Path) -> None:
    """``.env.bak`` had no checksum: a changed one was restored as it was."""
    folder = tmp_path / "b"
    folder.mkdir()
    (folder / ".env.bak").write_text("A=1\n", encoding="utf-8")
    backup.write_manifest(folder, databases={}, env_snapshot=".env.bak", volumes=[], migration_heads={})
    manifest = backup.read_manifest(folder)
    assert manifest is not None and backup.verify_backup(folder, manifest) == []
    (folder / ".env.bak").write_text("A=2\n", encoding="utf-8")
    assert backup.verify_backup(folder, manifest) == ["Checksum mismatch for .env.bak"]


@pytest.fixture
def no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backup's steps that reach Docker, answered with files."""

    def dump(folder: Path) -> dict[str, str]:
        (folder / "faden_backend.dump").write_bytes(b"DUMP")
        return {"faden_backend": "faden_backend.dump"}

    def volumes(folder: Path, **_: Any) -> list[str]:
        (folder / "faden_audit.tar.gz").write_bytes(b"AUDIT")
        return ["faden_audit.tar.gz"]

    monkeypatch.setattr(backup, "compose_up", lambda *_a, **_k: 0)
    monkeypatch.setattr(backup, "dump_databases", dump)
    monkeypatch.setattr(backup, "snapshot_volumes", volumes)
    monkeypatch.setattr(backup, "get_migration_heads", lambda _ctx: {"faden_backend": "abc"})


def _install(tmp_path: Path) -> tuple[Path, ComposeContext]:
    install = tmp_path / "fadenstack"
    install.mkdir()
    (install / ".env").write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
    return install, ComposeContext(env_file=install / ".env", project_dir=install)


@pytest.mark.usefixtures("no_docker")
def test_without_a_key_a_backup_is_made_as_before(tmp_path: Path) -> None:
    install, ctx = _install(tmp_path)
    result = backup.create_backup(ctx, output_dir=install / "backups")
    assert result.ok and not result.encrypted_to
    assert (result.backup_dir / "manifest.json").is_file() and (result.backup_dir / ".env.bak").is_file()
    assert "Backups are not encrypted" in (backup_key.reminder(install) or "")


@pytest.mark.usefixtures("no_docker")
def test_with_a_key_every_backup_is_encrypted_with_the_public_key_alone(tmp_path: Path) -> None:
    install, ctx = _install(tmp_path)
    key = backup_key.create(install, unattended=True)
    assert backup_key.pending(install) is not None

    result = backup.create_backup(ctx, output_dir=install / "backups", kind="nightly")

    assert result.ok, result.errors
    assert result.encrypted_to == key.id
    assert not (result.backup_dir / "manifest.json").exists() and not (result.backup_dir / ".env.bak").exists()
    assert backup_crypto.read_index(result.backup_dir)["kind"] == "nightly"  # type: ignore[index]
    opened = backup_crypto.open_backup(result.backup_dir, key)
    manifest = backup.read_manifest(opened)
    assert manifest is not None and backup.verify_backup(opened, manifest) == []
    assert (opened / ".env.bak").read_text(encoding="utf-8") == "POSTGRES_PASSWORD=pw\n"


@pytest.mark.usefixtures("no_docker")
def test_a_key_file_that_does_not_read_fails_the_backup_rather_than_leave_it_open(tmp_path: Path) -> None:
    install, ctx = _install(tmp_path)
    (install / backup_key.PUBLIC_FILE).write_text("FADEN-BACKUP-PUB1QQQQQ\n", encoding="utf-8")
    result = backup.create_backup(ctx, output_dir=install / "backups")
    assert not result.ok
    assert "does not read" in result.errors[0]


# ── The key round ─────────────────────────────────────────────────


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The groups asked for: 3 and 9 (shown as such), instead of random ones."""
    positions = [2, 8]
    monkeypatch.setattr(backup_key.secrets.SystemRandom, "sample", lambda _self, _pop, _k: list(positions))
    monkeypatch.setattr(click, "clear", lambda: None)
    return positions


def _run_create(install: Path, answers: list[str]) -> tuple[Any, RecoveryKey | None]:
    made: list[RecoveryKey] = []

    @click.command()
    def run() -> None:
        made.append(backup_key.create(install, unattended=False))

    result = CliRunner().invoke(run, input="\n".join(answers) + "\n")
    return result, (made[0] if made else None)


def test_the_key_is_kept_only_once_two_groups_of_it_are_typed_back(
    tmp_path: Path, asked: list[int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = RecoveryKey.generate()
    monkeypatch.setattr(RecoveryKey, "generate", staticmethod(lambda: fixed))
    _, parts = backup_crypto.groups(fixed.secret)

    result, key = _run_create(tmp_path, ["y", *(parts[i] for i in asked)])

    assert result.exit_code == 0, result.output
    assert "Group 3 of your copy" in result.output and "Group 9 of your copy" in result.output
    assert "matches" in result.output
    assert key is not None and key.id == fixed.id
    current = backup_key.recipient(tmp_path)
    assert current is not None and backup_crypto.key_id(current) == fixed.id
    assert backup_key.pending(tmp_path) is None, "a key shown to the person is never written to the server"


def test_stopping_before_the_copy_is_kept_makes_no_key(tmp_path: Path, asked: list[int]) -> None:
    result, key = _run_create(tmp_path, ["n"])
    assert result.exit_code != 0 and key is None
    assert backup_key.recipient(tmp_path) is None


def test_three_wrong_groups_show_the_key_again(
    tmp_path: Path, asked: list[int], monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = RecoveryKey.generate()
    monkeypatch.setattr(RecoveryKey, "generate", staticmethod(lambda: fixed))
    _, parts = backup_crypto.groups(fixed.secret)
    result, key = _run_create(tmp_path, ["y", "aaaaa", "bbbbb", "ccccc", "y", parts[2], parts[8]])
    assert result.exit_code == 0, result.output
    assert result.output.count("Backup recovery key") == 2
    assert key is not None and key.id == fixed.id


def test_a_key_written_to_a_file_is_read_back_and_never_into_the_install(tmp_path: Path) -> None:
    install = tmp_path / "fadenstack"
    install.mkdir()
    with pytest.raises(click.UsageError, match="inside the install folder"):
        backup_key.create(install, unattended=False, write_to=install / "key.txt")
    usb = tmp_path / "usb" / "key.txt"
    usb.parent.mkdir()
    key = backup_key.create(install, unattended=False, write_to=usb)
    assert RecoveryKey.from_text(usb.read_text(encoding="utf-8")).id == key.id


def test_a_second_key_is_made_only_on_purpose(tmp_path: Path) -> None:
    backup_key.create(tmp_path, unattended=True)
    with pytest.raises(click.ClickException, match="--replace"):
        backup_key.create(tmp_path, unattended=True)


def test_an_unattended_key_waits_on_the_server_until_a_copy_is_proven(tmp_path: Path, asked: list[int]) -> None:
    key = backup_key.create(tmp_path, unattended=True)
    left = backup_key.pending(tmp_path)
    assert left is not None and key.secret in left.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(left.stat().st_mode) == 0o600
    assert "backup key confirm" in (backup_key.reminder(tmp_path) or "")
    _, parts = backup_crypto.groups(key.secret)

    @click.command()
    def run() -> None:
        backup_key.confirm(tmp_path)

    wrong = CliRunner().invoke(run, input="aaaaa\nbbbbb\nccccc\n")
    assert wrong.exit_code == 0 and backup_key.pending(tmp_path) is not None
    right = CliRunner().invoke(run, input=f"{parts[2]}\n{parts[8]}\n")
    assert right.exit_code == 0, right.output
    assert backup_key.pending(tmp_path) is None and backup_key.reminder(tmp_path) is None


def test_a_kept_copy_is_checked_against_the_install(tmp_path: Path) -> None:
    key = backup_key.create(tmp_path, unattended=True)
    assert backup_key.check(tmp_path, " ".join(backup_crypto.groups(key.secret)[1])) == key.id
    with pytest.raises(click.ClickException, match="not the backup key of this install"):
        backup_key.check(tmp_path, RecoveryKey.generate().secret)


# ── faden restore and faden backup key ────────────────────────────


class _Config:
    def __init__(self, install: Path) -> None:
        self.install_dir = str(install)
        self.install_path = install


def _encrypted_backup(tmp_path: Path) -> tuple[Path, Path, RecoveryKey]:
    install = tmp_path / "fadenstack"
    install.mkdir()
    folder = tmp_path / "backups" / "20261018T030000Z"
    folder.mkdir(parents=True)
    (folder / ".env.bak").write_text("A=1\n", encoding="utf-8")
    (folder / "faden_backend.dump").write_bytes(b"DUMP")
    backup.write_manifest(
        folder, databases={"faden_backend": "faden_backend.dump"}, env_snapshot=".env.bak", volumes=[],
        migration_heads={},
    )
    key = backup_key.create(install, unattended=True)
    backup_crypto.encrypt_backup(folder, backup_crypto.public_key(key.public), kind="manual")
    return install, folder, key


def test_restore_opens_an_encrypted_backup_with_the_recovery_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install, folder, key = _encrypted_backup(tmp_path)
    (install / backup_key.PENDING_FILE).unlink()  # kept off the server, as it should be
    monkeypatch.setattr(restore_command, "load_config", lambda: _Config(install))
    keyfile = tmp_path / "usb.txt"
    keyfile.write_text(key.secret + "\n", encoding="utf-8")

    args = [str(folder), "--verify-only", "--key-file", str(keyfile)]
    result = CliRunner().invoke(restore_command.restore_cmd, args)

    assert result.exit_code == 0, result.output
    assert "Checksums OK" in result.output
    assert not (folder / backup_crypto.OPENED).exists(), "the decrypted copy is removed when the restore ends"

    typed = CliRunner().invoke(restore_command.restore_cmd, [str(folder), "--verify-only"], input=f"{key.secret}\n")
    assert typed.exit_code == 0, typed.output

    wrong = CliRunner().invoke(
        restore_command.restore_cmd, [str(folder), "--verify-only"],
        input="\n".join([RecoveryKey.generate().secret] * 3) + "\n",
    )
    assert wrong.exit_code == 1
    assert "does not open with that key" in wrong.output


def test_restore_uses_the_key_an_unattended_deploy_left_on_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install, folder, _key = _encrypted_backup(tmp_path)
    monkeypatch.setattr(restore_command, "load_config", lambda: _Config(install))
    result = CliRunner().invoke(restore_command.restore_cmd, [str(folder), "--verify-only"])
    assert result.exit_code == 0, result.output
    assert "still on this server" in result.output


def test_backup_key_check_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_cli.commands import backup as backup_command  # noqa: PLC0415

    monkeypatch.setattr(backup_command, "load_config", lambda: _Config(tmp_path))
    status = CliRunner().invoke(backup_cmd, ["key", "status"])
    assert "not encrypted" in " ".join(status.output.split())
    key = backup_key.create(tmp_path, unattended=True)
    ok = CliRunner().invoke(backup_cmd, ["key", "check"], input=key.secret + "\n")
    assert ok.exit_code == 0 and key.id in ok.output
    bad = CliRunner().invoke(backup_cmd, ["key", "check"], input=RecoveryKey.generate().secret + "\n")
    assert bad.exit_code == 1


# ── In faden deploy and faden upgrade ─────────────────────────────


def test_an_unattended_deploy_makes_the_key_and_leaves_it_to_be_moved(tmp_path: Path) -> None:
    backup_key.key_step(tmp_path, yes=True, deploy=True)
    assert backup_key.recipient(tmp_path) is not None
    assert backup_key.pending(tmp_path) is not None


def test_an_unattended_upgrade_leaves_backups_as_they_were(tmp_path: Path) -> None:
    backup_key.key_step(tmp_path, yes=True, deploy=False)
    assert backup_key.recipient(tmp_path) is None and backup_key.pending(tmp_path) is None


def test_someone_at_the_terminal_may_make_it_later(tmp_path: Path) -> None:
    @click.command()
    def run() -> None:
        backup_key.key_step(tmp_path, yes=False, deploy=False)

    result = CliRunner().invoke(run, input="n\n")
    assert result.exit_code == 0, result.output
    assert backup_key.recipient(tmp_path) is None
    assert "faden backup key create" in " ".join(result.output.split())


def test_an_install_with_a_key_is_not_asked_again(tmp_path: Path) -> None:
    backup_key.create(tmp_path, unattended=True)
    (tmp_path / backup_key.PENDING_FILE).unlink()

    @click.command()
    def run() -> None:
        backup_key.key_step(tmp_path, yes=False, deploy=False)

    result = CliRunner().invoke(run, input="")
    assert result.exit_code == 0 and "Make the backup key now" not in result.output
