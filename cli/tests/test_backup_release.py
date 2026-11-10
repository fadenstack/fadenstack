"""A backup records the release the install ran, and a restore says which release it holds.

An upgrade takes its backup with the new CLI, before anything changes. Its manifest recorded only that CLI's
version (``faden_version: 0.7.0rc1``) for data of the release before (``.env`` ``VERSION=0.6.0``, migration heads
of 0.6.0); the restore showed it as the backup's "faden version", and Help's "Rebuild a lost server" took that line
as the release to install (a test server, 10 November 2026). The manifest now records ``release`` beside it.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from faden_cli.commands import restore
from faden_cli.core import backup


def _text(output: str) -> str:
    """The command's output with rich's line wrapping undone."""
    return " ".join(output.split())


def test_the_manifest_records_the_installs_release(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("VERSION=0.6.0\nREGISTRY=localhost:5000/fadenstack\n", encoding="utf-8")
    assert backup.install_release(env) == "0.6.0"
    assert backup.install_release(tmp_path / "missing") == ""
    backup.write_manifest(tmp_path, databases={}, env_snapshot="", volumes=[], migration_heads={},
                          kind="upgrade", release=backup.install_release(env))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release"] == "0.6.0" and manifest["faden_version"]
    assert backup.describe_release(manifest) == f"Backup of release 0.6.0, taken by faden {manifest['faden_version']}"


def test_a_backup_without_release_says_so(tmp_path: Path) -> None:
    upgrade = {"faden_version": "0.7.0rc1", "kind": "upgrade"}
    nightly = {"faden_version": "0.6.0", "kind": "nightly"}
    assert backup.describe_release(upgrade) == (
        "Backup taken by faden 0.7.0rc1; the manifest does not record the install's release (backups taken before "
        "0.7.0 do not): an upgrade's backup holds the release before that upgrade, older than 0.7.0rc1"
    )
    assert backup.describe_release(nightly).startswith("Backup taken by faden 0.6.0; the manifest does not record")
    assert "older than" not in backup.describe_release(nightly)

    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({**upgrade, "databases": {}, "version": 1}), encoding="utf-8")
    result = CliRunner().invoke(restore.restore_cmd, [str(folder), "--verify-only"])
    assert result.exit_code == 0, result.output
    assert "does not record the install's release" in _text(result.output)
