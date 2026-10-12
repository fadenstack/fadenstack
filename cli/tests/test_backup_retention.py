"""``--retain`` keeps N backups of each kind, not N of whatever is in the directory.

The nightly, manual and upgrade backups share ``backups/``. Counting them together, a manual ``--retain 3``
cut the nightly set to 3, and every upgrade pushed a nightly backup out.
"""

from __future__ import annotations

import json
from pathlib import Path

from faden_cli.core import backup


def _make(root: Path, name: str, kind: str | None) -> None:
    folder = root / name
    folder.mkdir()
    manifest: dict = {"version": 1}
    if kind is not None:
        manifest["kind"] = kind
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _left(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir())


def test_a_manual_retain_leaves_the_nightly_and_upgrade_backups_alone(tmp_path: Path) -> None:
    for day in range(1, 8):
        _make(tmp_path, f"2026010{day}T030000Z", "nightly")
    _make(tmp_path, "20260105T120000Z", "upgrade")
    for hour in range(1, 6):
        _make(tmp_path, f"2026010{hour}T200000Z", "manual")

    assert backup.rotate_backups(tmp_path, retain=3, kind="manual") == 2

    left = _left(tmp_path)
    assert [n for n in left if n.endswith("200000Z")] == ["20260103T200000Z", "20260104T200000Z", "20260105T200000Z"]
    assert len([n for n in left if n.endswith("030000Z")]) == 7
    assert "20260105T120000Z" in left


def test_each_kind_keeps_its_own_newest(tmp_path: Path) -> None:
    for day in range(1, 5):
        _make(tmp_path, f"2026010{day}T030000Z", "nightly")
        _make(tmp_path, f"2026010{day}T040000Z", "upgrade")

    backup.rotate_backups(tmp_path, retain=2, kind="nightly")

    assert _left(tmp_path) == [
        "20260101T040000Z", "20260102T040000Z", "20260103T030000Z", "20260103T040000Z",
        "20260104T030000Z", "20260104T040000Z",
    ]


def test_a_backup_without_a_kind_counts_as_manual(tmp_path: Path) -> None:
    _make(tmp_path, "20260101T000000Z", None)
    _make(tmp_path, "20260102T000000Z", "manual")
    _make(tmp_path, "20260103T000000Z", "nightly")

    assert backup.rotate_backups(tmp_path, retain=1, kind="manual") == 1
    assert _left(tmp_path) == ["20260102T000000Z", "20260103T000000Z"]
    assert backup.rotate_backups(tmp_path, retain=1, kind="nightly") == 0


def test_a_backup_with_an_unreadable_manifest_is_never_removed(tmp_path: Path) -> None:
    _make(tmp_path, "20260102T000000Z", "manual")
    broken = tmp_path / "20260101T000000Z"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    assert backup.rotate_backups(tmp_path, retain=1, kind="manual") == 0
    assert broken.exists()


def test_the_manifest_records_the_kind(tmp_path: Path) -> None:
    backup.write_manifest(tmp_path, databases={}, env_snapshot="", volumes=[], migration_heads={}, kind="nightly")

    assert backup.read_manifest(tmp_path)["kind"] == "nightly"  # type: ignore[index]
