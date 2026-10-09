"""Backups carry the certificate material: a restored server is one the machines still trust.

Without the install's authority, a server rebuilt from a backup made a new
one, and every machine that had followed it to HTTPS refused to connect.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_tls import NAMES

from faden_cli.core import backup, tls


@pytest.fixture(autouse=True)
def fixed_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tls, "machine_names", lambda: list(NAMES))


def test_the_certificate_material_goes_into_the_backup_and_back(tmp_path: Path) -> None:
    install, backup_dir, rebuilt = tmp_path / "install", tmp_path / "backup", tmp_path / "rebuilt"
    install.mkdir()
    backup_dir.mkdir()
    rebuilt.mkdir()
    tls.generate(install)

    copied = backup.snapshot_tls(install, backup_dir)
    assert set(copied) >= {"tls/edge.crt", "tls/edge.key", "tls/ca.crt", "tls-ca/ca.crt", "tls-ca/ca.key"}
    backup.write_manifest(
        backup_dir, databases={}, env_snapshot="", volumes=[], migration_heads={}, tls_material=copied
    )
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert set(manifest["tls_material"]) == set(copied)
    assert backup.verify_backup(backup_dir, manifest) == []

    restored = backup.restore_tls(backup_dir, rebuilt, list(manifest["tls_material"]))
    assert set(restored) == set(copied)
    for rel in copied:
        assert (rebuilt / rel).read_bytes() == (install / rel).read_bytes()
    # The same authority: a certificate made on the rebuilt server is one the machines already trust.
    assert not tls.generate(rebuilt).trust_changed
    if sys.platform != "win32":
        assert (rebuilt / "tls-ca" / "ca.key").stat().st_mode & 0o777 == 0o600
        assert (rebuilt / "tls-ca").stat().st_mode & 0o777 == 0o700
        # In the backup every file is its owner's only; restored, a certificate is readable again, as nginx
        # and the machines need it.
        assert all(
            (backup_dir / "tls-material" / rel).stat().st_mode & 0o777 == 0o600 for rel in copied
        )
        assert (rebuilt / "tls" / "edge.crt").stat().st_mode & 0o777 == 0o644
        assert (rebuilt / "tls-ca" / "ca.crt").stat().st_mode & 0o777 == 0o644


def test_a_tampered_certificate_file_fails_the_check(tmp_path: Path) -> None:
    install, backup_dir = tmp_path / "install", tmp_path / "backup"
    install.mkdir()
    backup_dir.mkdir()
    tls.generate(install)
    copied = backup.snapshot_tls(install, backup_dir)
    backup.write_manifest(
        backup_dir, databases={}, env_snapshot="", volumes=[], migration_heads={}, tls_material=copied
    )
    (backup_dir / "tls-material" / "tls-ca" / "ca.crt").write_text("swapped", encoding="utf-8")
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert backup.verify_backup(backup_dir, manifest) == ["Checksum mismatch for tls-ca/ca.crt"]


def test_restore_writes_only_the_files_it_backs_up(tmp_path: Path) -> None:
    backup_dir, rebuilt = tmp_path / "backup", tmp_path / "rebuilt"
    (backup_dir / "tls-material").mkdir(parents=True)
    rebuilt.mkdir()
    (backup_dir / "tls-material" / "evil").write_text("x", encoding="utf-8")
    assert backup.restore_tls(backup_dir, rebuilt, ["../evil", "/etc/passwd", "tls/edge.crt"]) == []
    assert not (tmp_path / "evil").exists()
