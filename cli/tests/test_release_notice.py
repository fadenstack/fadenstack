"""The Preview notice: shown before deploy and upgrade go on, accepted by prompt or -y, and recorded."""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_cli import __version__
from faden_cli.core import release_notice
from faden_cli.core.settings import FadenConfig, load_config


@pytest.fixture(autouse=True)
def _config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_CONFIG", str(tmp_path / "faden.yaml"))


def test_dash_y_accepts_and_records_it(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = FadenConfig()
    release_notice.accept_preview(cfg, yes=True)
    assert "is a Preview release" in capsys.readouterr().out
    note = load_config().preview_accepted[__version__]
    assert note["how"] == "-y" and note["by"] and note["at"]


def test_a_prompt_that_is_declined_stops_and_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notice.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(release_notice.click, "confirm", lambda *a, **k: False)
    with pytest.raises(SystemExit):
        release_notice.accept_preview(FadenConfig(), yes=False)
    assert load_config().preview_accepted == {}


def test_a_prompt_that_is_accepted_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notice.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(release_notice.click, "confirm", lambda *a, **k: True)
    release_notice.accept_preview(FadenConfig(), yes=False)
    assert load_config().preview_accepted[__version__]["how"] == "prompt"


def test_without_a_terminal_or_dash_y_it_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_notice.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        release_notice.accept_preview(FadenConfig(), yes=False)


def test_a_dry_run_shows_it_and_records_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    release_notice.accept_preview(FadenConfig(), yes=False, dry_run=True)
    assert "is a Preview release" in capsys.readouterr().out
    assert load_config().preview_accepted == {}
