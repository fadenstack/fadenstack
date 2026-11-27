"""third_party_notices.py on a Mac: the libraries linked into libpython get their texts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the macOS part of the notices")

SCRIPT = Path(__file__).resolve().parents[1] / "third_party_notices.py"
spec = importlib.util.spec_from_file_location("third_party_notices", SCRIPT)
assert spec and spec.loader
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def test_every_mapped_licence_has_a_text() -> None:
    for licence in set(notices.MAC_STATIC.values()):
        assert (notices.LICENCE_TEXTS / f"{licence}.txt").is_file(), licence


def test_built_in_modules_bring_their_licences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "builtin_module_names", ("_ssl", "_hashlib", "_sqlite3", "sys"))
    text = "".join(notices._mac_libraries())
    assert "--- openssl (in _hashlib, _ssl) ---" in text
    assert "--- sqlite (in _sqlite3) ---" in text
    assert "--- xz" not in text  # not built in here, so not claimed


def test_a_missing_text_stops_the_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "builtin_module_names", ("_ssl",))
    monkeypatch.setattr(notices, "LICENCE_TEXTS", tmp_path)
    with pytest.raises(SystemExit, match="openssl"):
        notices._mac_libraries()


def test_system_links_are_only_the_systems(tmp_path: Path) -> None:
    links = notices._system_links(Path("/bin/ls"))
    assert links
    assert all(path.startswith(("/usr/lib/", "/System/")) for path in links)
