"""The count of changes to the system settings moves with each change to a stored value or secret, and only then."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.system_settings import SystemSettingSecret, SystemSettingValue


async def _epoch(db: AsyncSession) -> int:
    return int((await db.execute(text("SELECT epoch FROM system_settings_epoch WHERE id = 1"))).scalar_one())


@pytest.mark.anyio
async def test_each_change_to_a_value_moves_the_count(dbsession: AsyncSession) -> None:
    start = await _epoch(dbsession)
    dbsession.add(SystemSettingValue(key="updates.channel", value_json={"value": "preview"}, version=1))
    await dbsession.flush()
    inserted = await _epoch(dbsession)
    assert inserted != start, "an insert"

    await dbsession.execute(
        text("UPDATE system_setting_value SET value_json = '{\"value\": \"stable\"}' WHERE key = 'updates.channel'"),
    )
    updated = await _epoch(dbsession)
    assert updated not in {start, inserted}, "an update"

    await dbsession.execute(text("DELETE FROM system_setting_value WHERE key = 'updates.channel'"))
    assert await _epoch(dbsession) not in {start, inserted, updated}, "a delete"


@pytest.mark.anyio
async def test_each_change_to_a_secret_moves_the_count(dbsession: AsyncSession) -> None:
    start = await _epoch(dbsession)
    dbsession.add(SystemSettingSecret(key="faden_gateway.mcp_service_token", ciphertext="x", kek_version="v1"))
    await dbsession.flush()
    inserted = await _epoch(dbsession)
    assert inserted != start, "an insert"

    await dbsession.execute(
        text("UPDATE system_setting_secret SET ciphertext = 'y' WHERE key = 'faden_gateway.mcp_service_token'"),
    )
    updated = await _epoch(dbsession)
    assert updated not in {start, inserted}, "an update"

    await dbsession.execute(text("DELETE FROM system_setting_secret WHERE key = 'faden_gateway.mcp_service_token'"))
    assert await _epoch(dbsession) not in {start, inserted, updated}, "a delete"


@pytest.mark.anyio
async def test_a_number_never_comes_back_after_a_rollback(dbsession: AsyncSession) -> None:
    """A process may have read the number of a change that was then rolled back; the next one differs."""
    start = await _epoch(dbsession)
    nested = await dbsession.begin_nested()
    await dbsession.execute(text("DELETE FROM system_setting_value WHERE key = 'updates.channel'"))
    rolled_back = await _epoch(dbsession)
    await nested.rollback()
    assert await _epoch(dbsession) == start

    await dbsession.execute(text("DELETE FROM system_setting_value WHERE key = 'updates.channel'"))
    assert await _epoch(dbsession) not in {start, rolled_back}


@pytest.mark.anyio
async def test_other_writes_leave_it(dbsession: AsyncSession) -> None:
    start = await _epoch(dbsession)
    await dbsession.execute(text("SELECT count(*) FROM system_setting_value"))
    await dbsession.execute(text("UPDATE access_epoch SET epoch = epoch WHERE id = 1"))
    assert await _epoch(dbsession) == start
