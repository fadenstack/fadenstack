"""What people wrote is sealed at rest: project names, file names, tool errors, and the files themselves."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from faden_gateway.db import crypto
from faden_gateway.db.meta import meta
from faden_gateway.db.models.gateway import ChatProject
from faden_gateway.services.gateway.file_store import (
    FileSealError,
    LocalFileStore,
    SealedFileStore,
    is_sealed,
    open_bytes,
)
from faden_gateway.services.gateway.service import tool_error_kind
from faden_gateway.settings import settings
from faden_gateway.tools import rotate_keys, seal_plaintext

KEY = "this-install-chat-key-0123456789"
NEW = "the-next-chat-key-9876543210"
PICTURE = b"\x89PNG\r\n\x1a\n" + b"pixels" * 100


@pytest.fixture(autouse=True)
def _restore_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_key_provider", None)
    monkeypatch.setattr(crypto, "_previous_provider", None)


@pytest.mark.anyio
async def test_a_file_is_sealed_on_disk_and_read_back(tmp_path: Path) -> None:
    crypto.configure(KEY)
    store = SealedFileStore(LocalFileStore(tmp_path))
    await store.put_bytes("t/s/f1", PICTURE)
    on_disk = (tmp_path / "t" / "s" / "f1").read_bytes()
    assert is_sealed(on_disk) and b"pixels" not in on_disk
    assert await store.get_bytes("t/s/f1") == PICTURE


@pytest.mark.anyio
async def test_a_file_from_before_is_read_as_it_is(tmp_path: Path) -> None:
    (tmp_path / "t" / "s").mkdir(parents=True)
    (tmp_path / "t" / "s" / "old.png").write_bytes(PICTURE)  # stored before files were sealed
    crypto.configure(KEY)
    assert await SealedFileStore(LocalFileStore(tmp_path)).get_bytes("t/s/old.png") == PICTURE


def test_seal_files_seals_plain_files_once(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one").write_bytes(PICTURE)
    (tmp_path / "a" / "two").write_bytes(b"notes")
    first = seal_plaintext.seal_files(tmp_path, KEY)
    assert (first.sealed, first.already) == (2, 0)
    again = seal_plaintext.seal_files(tmp_path, KEY)
    assert (again.sealed, again.already) == (0, 2)
    key = crypto.derive_key(KEY, "chat-files")
    assert open_bytes((tmp_path / "a" / "one").read_bytes(), [key]) == PICTURE
    assert not list(tmp_path.rglob("*.sealing"))


@pytest.mark.anyio
async def test_the_chat_key_rotation_reseals_the_files_while_it_has_the_old_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = tmp_path / "chat-files"
    crypto.configure(KEY)
    store = SealedFileStore(LocalFileStore(files))
    await store.put_bytes("t/s/f1", PICTURE)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    monkeypatch.setattr(settings, "encryption_key", NEW)
    monkeypatch.setattr(settings, "encryption_key_previous", KEY)
    monkeypatch.setattr(settings, "chat_file_store_root", str(files))
    try:
        outcome = await rotate_keys.run("chat", skip_unreadable=False, engine=engine)
    finally:
        await engine.dispose()
    assert outcome["ok"], outcome
    assert outcome["files"]["resealed"] == 1

    crypto.configure(NEW)  # the old key dropped, as the CLI does at the end
    assert await store.get_bytes("t/s/f1") == PICTURE
    with pytest.raises(FileSealError):
        open_bytes((files / "t" / "s" / "f1").read_bytes(), [crypto.derive_key(KEY, "chat-files")])


@pytest.mark.anyio
async def test_project_names_and_descriptions_are_sealed_at_rest(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    crypto.configure(KEY)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(ChatProject).values(
                id=uuid.uuid4(), tenant_id="t", user_id="u", name="Dismissal of Mr Huber",
                description="Notes for the works council", metadata_json={"colour": "red"},
            ))
        async with engine.connect() as conn:
            raw = (await conn.execute(sa.text("SELECT name, description, metadata_json FROM chat_project"))).one()
            assert all(str(value).startswith("gAAAAA") for value in raw)
            row = (await conn.execute(sa.select(ChatProject.name, ChatProject.metadata_json))).one()
        assert (row.name, row.metadata_json) == ("Dismissal of Mr Huber", {"colour": "red"})
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        ("Tool call failed: connection reset", "failed"),
        ("The request timed out after 30 s", "timeout"),
        ("Permission denied for /etc/shadow", "refused"),
        ("Unknown column 'salary' in Huber's record", "error"),
    ],
)
def test_the_console_sees_the_kind_of_a_tool_failure_not_its_text(content: str, kind: str) -> None:
    assert tool_error_kind(content) == kind
