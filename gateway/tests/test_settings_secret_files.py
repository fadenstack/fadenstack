"""The keys that open content come from the files compose mounts under /run/secrets (admin guardrails, 6.2).

They were in the container's environment, which `docker inspect` and the console's container pages showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_gateway.settings import Settings


def test_the_key_is_read_from_its_mounted_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FADEN_GATEWAY_ENCRYPTION_KEY", raising=False)
    (tmp_path / "FADEN_GATEWAY_ENCRYPTION_KEY").write_text("from-a-file\n", encoding="utf-8")
    assert Settings(_env_file=None, _secrets_dir=tmp_path).encryption_key == "from-a-file"


def test_the_environment_wins_over_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A compose file from before still passes it as environment: that one counts."""
    (tmp_path / "FADEN_GATEWAY_ENCRYPTION_KEY").write_text("from-a-file", encoding="utf-8")
    monkeypatch.setenv("FADEN_GATEWAY_ENCRYPTION_KEY", "from-the-environment")
    assert Settings(_env_file=None, _secrets_dir=tmp_path).encryption_key == "from-the-environment"


def test_the_mounted_file_wins_over_a_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env copied into an image held the development key; it outranked the mounted one."""
    monkeypatch.delenv("FADEN_GATEWAY_ENCRYPTION_KEY", raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "FADEN_GATEWAY_ENCRYPTION_KEY").write_text("from-the-mounted-file", encoding="utf-8")
    (tmp_path / ".env").write_text("FADEN_GATEWAY_ENCRYPTION_KEY=from-a-dotenv-file\n", encoding="utf-8")
    loaded = Settings(_env_file=tmp_path / ".env", _secrets_dir=secrets)
    assert loaded.encryption_key == "from-the-mounted-file"
