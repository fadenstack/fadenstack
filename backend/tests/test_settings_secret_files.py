"""The keys that open content come from the files compose mounts under /run/secrets (admin guardrails, 6.2).

They were in the container's environment, which `docker inspect` and the console's container pages showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_backend.settings import Settings


def test_the_key_is_read_from_its_mounted_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FADEN_BACKEND_CREDENTIAL_PEPPER", raising=False)
    (tmp_path / "FADEN_BACKEND_CREDENTIAL_PEPPER").write_text("from-a-file\n", encoding="utf-8")
    assert Settings(_env_file=None, _secrets_dir=tmp_path).credential_pepper == "from-a-file"


def test_the_environment_wins_over_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A compose file from before still passes it as environment: that one counts."""
    (tmp_path / "FADEN_BACKEND_CREDENTIAL_PEPPER").write_text("from-a-file", encoding="utf-8")
    monkeypatch.setenv("FADEN_BACKEND_CREDENTIAL_PEPPER", "from-the-environment")
    assert Settings(_env_file=None, _secrets_dir=tmp_path).credential_pepper == "from-the-environment"


def test_the_mounted_file_wins_over_a_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env copied into an image held the development key; it outranked the mounted one."""
    monkeypatch.delenv("FADEN_BACKEND_CREDENTIAL_PEPPER", raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "FADEN_BACKEND_CREDENTIAL_PEPPER").write_text("from-the-mounted-file", encoding="utf-8")
    (tmp_path / ".env").write_text("FADEN_BACKEND_CREDENTIAL_PEPPER=from-a-dotenv-file\n", encoding="utf-8")
    loaded = Settings(_env_file=tmp_path / ".env", _secrets_dir=secrets)
    assert loaded.credential_pepper == "from-the-mounted-file"
