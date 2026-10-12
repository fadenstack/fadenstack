"""``faden secrets rotate``, MCP's half: two keys that open, and the job that re-seals.

While a rotation is unfinished MCP holds the new encryption key and the old one: the headers and the
environment of every server (bearer tokens, API keys) open if either sealed them. The job turns them all
into values the new key alone opens, in one transaction.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles

from faden_mcp.db import crypto
from faden_mcp.db.models.mcp import Base, MCPServerModel
from faden_mcp.settings import settings
from faden_mcp.tools import key_rotation, rotate_keys
from faden_mcp.tools.key_rotation import RotationError, Scheme, Target, rotate

OLD = "the-old-key-0123456789"
NEW = "the-new-key-9876543210"
HEADERS = {"Authorization": "Bearer t0ken"}


@compiles(JSONB, "sqlite")
def _jsonb_in_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN003, ANN202
    """The models use Postgres types; the test database is SQLite."""
    return "JSON"


@pytest.fixture(autouse=True)
def _restore_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_key_provider", None)
    monkeypatch.setattr(crypto, "_previous_provider", None)


def test_headers_sealed_with_the_previous_key_still_open() -> None:
    column = crypto.EncryptedJSON("mcp-server-headers")
    crypto.configure(OLD)
    sealed = column.process_bind_param(HEADERS, None)
    crypto.configure(NEW, OLD)
    assert column.process_result_value(sealed, None) == HEADERS
    # What is written meanwhile is sealed with the new key alone.
    fresh = column.process_bind_param(HEADERS, None)
    crypto.configure(NEW)
    assert column.process_result_value(fresh, None) == HEADERS


def test_the_environment_uses_its_own_label() -> None:
    labels = {t.label: t for t in rotate_keys.targets(NEW, OLD)}
    assert {"mcp_servers.headers_json_encrypted", "mcp_servers.env_json_encrypted"} <= set(labels)
    headers = labels["mcp_servers.headers_json_encrypted"].scheme
    env = labels["mcp_servers.env_json_encrypted"].scheme
    token = headers.current.encrypt(b"x")
    assert not _opens(env.current, token)


def _opens(fernet, token: bytes) -> bool:  # noqa: ANN001
    from cryptography.fernet import InvalidToken  # noqa: PLC0415

    try:
        fernet.decrypt(token)
    except InvalidToken:
        return False
    return True


@pytest.mark.asyncio
async def test_servers_written_under_the_old_key_read_under_the_new_key_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        crypto.configure(OLD)
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(MCPServerModel).values(
                    id=uuid.uuid4(), name="github", tool_prefix="github", transport="streamable_http", url="http://x",
                    headers_json_encrypted=HEADERS, env_json_encrypted={"TOKEN": "abc"},
                ),
            )
        monkeypatch.setattr(settings, "encryption_key", NEW)
        monkeypatch.setattr(settings, "encryption_key_previous", OLD)
        crypto.configure(NEW, OLD)

        outcome = await rotate_keys.run(skip_unreadable=False, engine=engine)
        assert outcome["ok"], outcome
        assert outcome["columns"]["mcp_servers.headers_json_encrypted"]["rotated"] == 1
        assert outcome["columns"]["mcp_servers.env_json_encrypted"]["rotated"] == 1

        crypto.configure(NEW)  # the previous key dropped, as the CLI does at the end
        async with engine.connect() as conn:
            row = (await conn.execute(
                sa.select(MCPServerModel.headers_json_encrypted, MCPServerModel.env_json_encrypted),
            )).one()
        assert (row[0], row[1]) == (HEADERS, {"TOKEN": "abc"})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_crash_midway_changes_nothing_and_a_rerun_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")
    scheme = Scheme(crypto.derive_fernet(NEW, "probe"), crypto.derive_fernet(OLD, "probe"))
    target = Target("probe", ("id",), "secret", scheme)
    old = crypto.derive_fernet(OLD, "probe")
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE probe (id integer PRIMARY KEY, secret text)"))
        for i in range(1, 5):
            await conn.execute(
                sa.text("INSERT INTO probe VALUES (:i, :s)"), {"i": i, "s": old.encrypt(f"v{i}".encode()).decode()},
            )

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
        new = crypto.derive_fernet(NEW, "probe")
        assert [new.decrypt(v.encode()).decode() for v in await stored()] == ["v1", "v2", "v3", "v4"]

        # A value the new key alone cannot open is never committed.
        monkeypatch.setattr(key_rotation, "reseal", lambda v, s: ("rotated", old.encrypt(b"x").decode()))
        with pytest.raises(RotationError):
            await rotate(engine, [target], progress=lambda _: None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_with_no_key_there_is_nothing_to_rotate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "")
    outcome = await rotate_keys.run(skip_unreadable=False)
    assert outcome["ok"] and outcome["columns"] == {}
