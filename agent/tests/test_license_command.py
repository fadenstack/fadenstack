"""`faden-agent license` reaches the terms of everything the binary carries.

The binary bundles a Python, the agent's packages and a few system libraries
beside our own code. Our LICENSE and NOTICE cover only the agent; the other
licences travel as THIRD-PARTY-NOTICES.txt, which `license` points at and
`license --third-party` prints.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from faden_agent import __main__ as agent_main


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where a one-file binary unpacks its data, with LICENSE and NOTICE."""
    (tmp_path / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    (tmp_path / "NOTICE").write_text("Fadenstack\nCopyright 2026\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    # A frozen main() hands children the machine's library path; keep the
    # test process's own.
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    return tmp_path


def _notices(bundle: Path) -> None:
    (bundle / "THIRD-PARTY-NOTICES.txt").write_text(
        "certifi\nMozilla Public License 2.0 (MPL 2.0)\n", encoding="utf-8"
    )


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["faden-agent", "license", *args])
    agent_main.main()


def test_license_points_at_the_third_party_notices(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _notices(bundle)
    _run(monkeypatch)
    out = capsys.readouterr().out
    assert "Apache License" in out
    assert "Fadenstack" in out
    assert "faden-agent license --third-party" in out
    assert "Mozilla Public License" not in out


def test_third_party_prints_the_notices(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _notices(bundle)
    _run(monkeypatch, "--third-party")
    out = capsys.readouterr().out
    assert "Mozilla Public License 2.0" in out
    assert "Apache License" not in out


def test_a_binary_without_notices_does_not_point_at_them(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(monkeypatch)
    out = capsys.readouterr().out
    assert "Apache License" in out
    assert "--third-party" not in out


def test_a_binary_without_notices_says_so_and_fails(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        _run(monkeypatch, "--third-party")
    assert exited.value.code == 1
    assert "built without its third-party notices" in capsys.readouterr().err


def test_a_package_install_names_where_the_licences_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    _run(monkeypatch, "--third-party")
    assert "installed beside it, each with its own licence" in capsys.readouterr().out
