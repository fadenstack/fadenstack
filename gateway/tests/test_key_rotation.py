"""``faden secrets rotate``, the gateway's half: two keys that open, and the job that re-seals.

While a rotation is unfinished the gateway holds the new key and the old one and must open what either
sealed -- the provider keys and the JWT secret the backend sealed, the machine certificate key, and its
own chat columns. The job turns every sealed value into one the new key alone opens, in one transaction.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import create_async_engine

from faden_gateway.db import crypto
from faden_gateway.db.meta import meta
from faden_gateway.db.models.gateway import MemoryFact
from faden_gateway.services.gateway import upstream_tls
from faden_gateway.settings import settings
from faden_gateway.tools import key_rotation, rotate_keys
from faden_gateway.tools.key_rotation import RotationError, Scheme, Target, rotate

OLD = "the-old-key-0123456789"
NEW = "the-new-key-9876543210"


def _backend_seal(master_key: str, value: str) -> str:
    """What the backend's SettingsCrypto.encrypt does."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key.encode()).digest())).encrypt(
        value.encode()
    ).decode()


@pytest.fixture(autouse=True)
def _restore_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_key_provider", None)
    monkeypatch.setattr(crypto, "_previous_provider", None)


# ── the second key ────────────────────────────────────────────────


def test_a_provider_key_the_backend_sealed_with_the_previous_key_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    assert crypto.decrypt_provider_key(_backend_seal(OLD, "sk-1")) == "sk-1"
    assert crypto.decrypt_provider_key(_backend_seal(NEW, "sk-2")) == "sk-2"
    with pytest.raises(InvalidToken):
        crypto.decrypt_provider_key(_backend_seal("somebody-else", "sk-3"))


def test_chat_columns_sealed_with_either_chat_key_open() -> None:
    column = crypto.EncryptedText("chat-content")
    crypto.configure(OLD)
    sealed_old = column.process_bind_param("hello", None)
    crypto.configure(NEW, OLD)
    assert column.process_result_value(sealed_old, None) == "hello"
    sealed_new = column.process_bind_param("hello again", None)
    crypto.configure(NEW)
    assert column.process_result_value(sealed_new, None) == "hello again"
    with pytest.raises(InvalidToken):
        column.process_result_value(sealed_old, None)


def test_a_reply_event_sealed_before_the_rotation_still_unseals() -> None:
    crypto.configure(OLD)
    sealed = crypto.seal("chat-events", b"event")
    assert sealed is not None
    crypto.configure(NEW, OLD)
    assert crypto.unseal("chat-events", sealed) == b"event"


def test_the_machine_certificate_key_the_backend_sealed_opens_with_either_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = upstream_tls._CLIENT_KEY_PURPOSE
    sealed = crypto.derive_fernet(OLD, purpose).encrypt(b"KEY-PEM").decode()
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    assert upstream_tls._unseal_client_key(sealed) == "KEY-PEM"


# ── the job ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_chat_rows_written_under_the_old_key_read_under_the_new_key_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    try:
        crypto.configure(OLD)
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(MemoryFact).values(id=uuid.uuid4(), tenant_id="t", user_id="u", key="colour", value="blue"),
            )
        monkeypatch.setattr(settings, "encryption_key", NEW)
        monkeypatch.setattr(settings, "encryption_key_previous", OLD)
        crypto.configure(NEW, OLD)

        outcome = await rotate_keys.run("chat", skip_unreadable=False, engine=engine)
        assert outcome["ok"], outcome
        assert outcome["columns"]["memory_fact.key"]["rotated"] == 1
        assert outcome["columns"]["memory_fact.value"]["rotated"] == 1

        crypto.configure(NEW)  # the previous key dropped, as the CLI does at the end
        async with engine.connect() as conn:
            row = (await conn.execute(sa.select(MemoryFact.key, MemoryFact.value))).one()
        assert (row.key, row.value) == ("colour", "blue")
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_chat_rotation_with_no_chat_key_has_nothing_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "")
    outcome = await rotate_keys.run("chat", skip_unreadable=False)
    assert outcome["ok"] and outcome["columns"] == {}


@pytest.mark.anyio
async def test_a_provider_key_the_backend_sealed_is_rotated_with_the_settings_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE llm_provider_instance (id integer PRIMARY KEY, api_key_encrypted text)"))
        await conn.execute(sa.text("INSERT INTO llm_provider_instance VALUES (1, :k), (2, NULL), (3, 'sk-typed')"),
                           {"k": _backend_seal(OLD, "sk-live")})
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    try:
        outcome = await rotate_keys.run("settings", skip_unreadable=False, engine=engine)
        assert outcome["ok"], outcome
        counts = outcome["columns"]["llm_provider_instance.api_key_encrypted"]
        assert (counts["rotated"], counts["plaintext"]) == (1, 1)
        async with engine.connect() as conn:
            stored = (await conn.execute(sa.text("SELECT api_key_encrypted FROM llm_provider_instance WHERE id = 1"))).scalar_one()
        monkeypatch.setattr(settings, "settings_master_key_previous", "")
        assert crypto.decrypt_provider_key(stored) == "sk-live"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_crash_midway_changes_nothing_and_a_rerun_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")
    scheme = Scheme(crypto.settings_fernet_for(NEW), crypto.settings_fernet_for(OLD))
    target = Target("probe", ("id",), "secret", scheme)
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE probe (id integer PRIMARY KEY, secret text)"))
        for i in range(1, 5):
            await conn.execute(sa.text("INSERT INTO probe VALUES (:i, :s)"), {"i": i, "s": _backend_seal(OLD, f"v{i}")})

    async def stored() -> list[str]:
        async with engine.connect() as conn:
            return [r[0] for r in await conn.execute(sa.text("SELECT secret FROM probe ORDER BY id"))]

    before = await stored()
    real = key_rotation.reseal
    calls = {"n": 0}

    def crash(value: str, sch: Scheme) -> tuple[str, str | None]:
        calls["n"] += 1
        if calls["n"] == 3:  # noqa: PLR2004
            raise ConnectionError("gone")
        return real(value, sch)

    monkeypatch.setattr(key_rotation, "reseal", crash)
    try:
        with pytest.raises(ConnectionError):
            await rotate(engine, [target], progress=lambda _: None)
        assert await stored() == before
        monkeypatch.setattr(key_rotation, "reseal", real)
        report = await rotate(engine, [target], progress=lambda _: None)
        assert report.columns["probe.secret"].rotated == 4  # noqa: PLR2004
        new_only = crypto.settings_fernet_for(NEW)
        assert [new_only.decrypt(v.encode()).decode() for v in await stored()] == ["v1", "v2", "v3", "v4"]
        monkeypatch.setattr(key_rotation, "reseal", lambda v, s: ("rotated", _backend_seal(OLD, "x")))
        with pytest.raises(RotationError):
            await rotate(engine, [target], progress=lambda _: None)
    finally:
        await engine.dispose()


def test_every_chat_column_is_found_in_the_models() -> None:
    targets = rotate_keys.chat_targets(NEW, OLD)
    labels = {t.label for t in targets}
    assert {"memory_fact.key", "memory_fact.value"} <= labels
    for t in targets:
        assert t.column in meta.tables[t.table].c
        assert t.primary_key
