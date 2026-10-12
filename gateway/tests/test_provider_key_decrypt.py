"""The gateway opens provider keys the backend sealed with the settings master key."""

from __future__ import annotations

import base64
import hashlib
import importlib
import logging

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from faden_gateway.db import crypto
from faden_gateway.services.gateway.llm_adapter import _resolve_api_key
from faden_gateway.settings import settings

KEY = "sk-live-0123456789abcdef"


def _seal(master_key: str, value: str) -> str:
    """What the backend's SettingsCrypto.encrypt does."""
    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key.encode()).digest()))
    return fernet.encrypt(value.encode()).decode()


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    crypto._warned_plaintext.clear()


def test_backend_ciphertext_is_opened() -> None:
    assert crypto.decrypt_provider_key(_seal(settings.settings_master_key, KEY)) == KEY
    assert _resolve_api_key(_seal(settings.settings_master_key, KEY)) == KEY


def test_chat_encryption_key_does_not_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_key_provider", None)  # chat columns unencrypted
    assert crypto.decrypt_provider_key(_seal(settings.settings_master_key, KEY)) == KEY


def test_plaintext_is_used_but_warned_about_once(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=crypto.logger.name):
        assert crypto.decrypt_provider_key(KEY) == KEY
        assert crypto.decrypt_provider_key(KEY) == KEY
        assert crypto.decrypt_provider_key("sk-other") == "sk-other"
    warnings = [r for r in caplog.records if "unencrypted" in r.getMessage()]
    assert len(warnings) == 2  # one per distinct key
    assert KEY not in caplog.text


def test_wrong_master_key_raises() -> None:
    with pytest.raises(InvalidToken):
        crypto.decrypt_provider_key(_seal("another-master-key", KEY))


def test_empty_master_key_raises_for_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = _seal(settings.settings_master_key, KEY)
    monkeypatch.setattr(settings, "settings_master_key", "")
    with pytest.raises(InvalidToken):
        crypto.decrypt_provider_key(sealed)


def test_resolver_sends_no_auth_for_unreadable_ciphertext() -> None:
    assert _resolve_api_key(_seal("another-master-key", KEY)) is None
    assert _resolve_api_key(None) is None


def test_migration_seals_plaintext_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = importlib.import_module("faden_gateway.db.migrations.versions.2026-10-12-10-01_pr0vk3yg")
    # An install's own key: the migration refuses the published development one (test below).
    monkeypatch.setattr(settings, "settings_master_key", "an-installs-own-master-key")
    engine = sa.create_engine("sqlite://")
    sealed = _seal(settings.settings_master_key, "sk-already")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE llm_provider_instance (id integer primary key, api_key_encrypted text)"))
        for row_id, value in enumerate([KEY, sealed, None, ""], start=1):
            conn.execute(
                sa.text("INSERT INTO llm_provider_instance VALUES (:i, :v)"), {"i": row_id, "v": value},
            )
        monkeypatch.setattr(mig.op, "get_bind", lambda: conn)
        mig.upgrade()
        mig.upgrade()  # idempotent
        rows = dict(conn.execute(sa.text("SELECT id, api_key_encrypted FROM llm_provider_instance")).fetchall())
    assert crypto.decrypt_provider_key(rows[1]) == KEY
    assert rows[1] != KEY
    assert rows[2] == sealed
    assert rows[3] is None
    assert rows[4] == ""


def test_migration_without_master_key_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = importlib.import_module("faden_gateway.db.migrations.versions.2026-10-12-10-01_pr0vk3yg")
    engine = sa.create_engine("sqlite://")
    monkeypatch.setattr(settings, "settings_master_key", "")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE llm_provider_instance (id integer primary key, api_key_encrypted text)"))
        conn.execute(sa.text("INSERT INTO llm_provider_instance VALUES (1, :v)"), {"v": KEY})
        monkeypatch.setattr(mig.op, "get_bind", lambda: conn)
        with pytest.raises(RuntimeError):
            mig.upgrade()


def test_migration_refuses_the_published_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sealing with the key everyone knows would only look like encryption."""
    mig = importlib.import_module("faden_gateway.db.migrations.versions.2026-10-12-10-01_pr0vk3yg")
    monkeypatch.setattr(settings, "settings_master_key", "dev-settings-master-key-change-me")
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_published_keys", False)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE llm_provider_instance (id integer primary key, api_key_encrypted text)"))
        conn.execute(sa.text("INSERT INTO llm_provider_instance VALUES (1, 'sk-plain')"))
        monkeypatch.setattr(mig.op, "get_bind", lambda: conn)
        with pytest.raises(RuntimeError, match="published development value"):
            mig.upgrade()
