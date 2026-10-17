"""``faden secrets seal``: chats written in plain text (before the install had a key) are sealed, nothing else."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from faden_gateway.db import crypto
from faden_gateway.db.meta import meta
from faden_gateway.db.models.gateway import MemoryFact
from faden_gateway.settings import settings
from faden_gateway.tools import seal_plaintext

KEY = "this-install-chat-key-0123456789"


@pytest.fixture(autouse=True)
def _restore_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_key_provider", None)
    monkeypatch.setattr(crypto, "_previous_provider", None)


@pytest.mark.anyio
async def test_plain_rows_are_sealed_once_and_still_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    try:
        # Written while the install had no key: plain text in the database.
        async with engine.begin() as conn:
            for n in range(3):
                await conn.execute(sa.insert(MemoryFact).values(
                    id=uuid.uuid4(), tenant_id="t", user_id="u", key=f"k{n}", value=f"plain {n}",
                ))
        monkeypatch.setattr(settings, "encryption_key", KEY)
        monkeypatch.setattr(settings, "encryption_key_previous", "")
        crypto.configure(KEY)
        # One more, written with the key: sealed already.
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MemoryFact).values(
                id=uuid.uuid4(), tenant_id="t", user_id="u", key="k3", value="sealed 3",
            ))

        outcome = await seal_plaintext.run(engine=engine)
        assert outcome["ok"], outcome
        assert outcome["columns"]["memory_fact.value"] == {"sealed": 3, "already": 4, "unreadable": 0}

        async with engine.connect() as conn:
            raw = (await conn.execute(sa.text("SELECT value FROM memory_fact"))).scalars().all()
            assert all(value.startswith("gAAAAA") for value in raw)
            read = sorted((await conn.execute(sa.select(MemoryFact.value))).scalars().all())
        assert read == ["plain 0", "plain 1", "plain 2", "sealed 3"]

        again = await seal_plaintext.run(engine=engine)
        assert again["columns"]["memory_fact.value"]["sealed"] == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_value_that_looks_sealed_but_opens_with_no_key_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    try:
        stranger = crypto.derive_fernet("somebody-elses-key", "chat-memory").encrypt(b"theirs").decode()
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MemoryFact).values(
                id=uuid.uuid4(), tenant_id="t", user_id="u", key="k", value="placeholder",
            ))
            await conn.execute(sa.text("UPDATE memory_fact SET value = :value"), {"value": stranger})
        monkeypatch.setattr(settings, "encryption_key", KEY)
        monkeypatch.setattr(settings, "encryption_key_previous", "")
        outcome = await seal_plaintext.run(engine=engine)
        assert outcome["columns"]["memory_fact.value"]["unreadable"] == 1
        async with engine.connect() as conn:
            assert (await conn.execute(sa.text("SELECT value FROM memory_fact"))).scalar_one() == stranger
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_without_a_key_there_is_nothing_to_seal_with(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "")
    outcome = await seal_plaintext.run()
    assert not outcome["ok"] and "FADEN_GATEWAY_ENCRYPTION_KEY" in outcome["error"]
