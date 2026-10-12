"""Finding the machine's own Hugging Face cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_agent.config import detect_hf_cache


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_none_when_the_machine_has_no_cache(home: Path) -> None:
    assert detect_hf_cache() is None


def test_the_users_home_cache(home: Path) -> None:
    hub = home / ".cache" / "huggingface" / "hub"
    hub.mkdir(parents=True)
    assert detect_hf_cache() == str(hub.resolve())


def test_hf_home_beats_home_and_hub_cache_beats_hf_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / ".cache" / "huggingface" / "hub").mkdir(parents=True)
    (home / "hfhome" / "hub").mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(home / "hfhome"))
    assert detect_hf_cache() == str((home / "hfhome" / "hub").resolve())
    (home / "direct").mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(home / "direct"))
    assert detect_hf_cache() == str((home / "direct").resolve())


def test_a_set_variable_naming_a_missing_directory_falls_through(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = home / ".cache" / "huggingface" / "hub"
    hub.mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(home / "gone"))
    assert detect_hf_cache() == str(hub.resolve())


def test_the_model_store_itself_is_not_reported(home: Path) -> None:
    hub = home / ".cache" / "huggingface" / "hub"
    hub.mkdir(parents=True)
    assert detect_hf_cache(str(hub)) is None
