"""What a device reports about itself (sdk/51 8.3), and the arguments' hash on decision records (8.10).

The manifest's ``device`` block -- the system, the app's version, its plugins and local MCP programs with their
SHA-256 -- is the device's: kept per device, the latest report replacing the one before, never in the manifest the
console compares across devices. A program is kept by its file name only. It goes with the person's account, and
after 90 days without a report.

``approval.decided`` and ``tool.executed`` take ``arguments_sha256``: kept as sent, never recomputed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AgentClientEvent, AgentClientManifest, AgentDeviceInventory
from faden_gateway.services.gateway import agent_manifests, erasure
from tests.test_agent_routes import (  # noqa: F401 - fixtures
    AGENT_ID,
    MANIFEST,
    MANIFEST_URL,
    USER,
    _agent_token,
    _Backend,
    _headers,
    backend,
    manifests,
)

SHA = "a" * 64
OTHER_SHA = "b" * 64
DEVICE: dict[str, Any] = {
    "os": "Windows 11 10.0.26100 x64",
    "app_version": "0.3.0",
    "plugins": [{"name": "contoso-crm", "version": "1.0.0", "sha256": SHA}],
    "mcp_programs": [
        {"name": "files", "program": "C:\\Users\\alice\\mcp\\server-filesystem.exe", "sha256": OTHER_SHA},
        {"name": "git", "program": "/home/alice/bin/mcp-git", "sha256": SHA},
        {"name": "notes", "sha256": SHA},
    ],
}


async def _devices(db_session: AsyncSession) -> list[AgentDeviceInventory]:
    db_session.expire_all()
    return list((await db_session.execute(select(AgentDeviceInventory))).scalars())


@pytest.mark.anyio
async def test_a_device_block_is_kept_per_device_and_not_in_the_manifest(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, manifests: None,  # noqa: F811
) -> None:
    plain = await client.put(MANIFEST_URL, headers=_headers(), json=MANIFEST)
    with_device = await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "device": DEVICE})
    assert with_device.status_code == 200, with_device.text
    assert with_device.json()["sha256"] == plain.json()["sha256"], "the device block is not the manifest's"
    [kept] = list((await db_session.execute(select(AgentClientManifest))).scalars())
    assert "device" not in kept.manifest and "alice" not in json.dumps(kept.manifest)

    [row] = await _devices(db_session)
    assert (row.agent_id, row.device_id, row.user_id, row.tenant_id) == (AGENT_ID, "device-7", USER, "tenant-a")
    assert (row.os, row.app_version) == ("Windows 11 10.0.26100 x64", "0.3.0")
    assert row.plugins == [{"name": "contoso-crm", "version": "1.0.0", "sha256": SHA}]
    assert row.mcp_programs == [
        {"name": "files", "program": "server-filesystem.exe", "sha256": OTHER_SHA},
        {"name": "git", "program": "mcp-git", "sha256": SHA},
        {"name": "notes", "sha256": SHA},
    ], "a program by its file name only: no folder, so no user name"
    first = row.reported_at

    # The next report replaces it; another device has its own.
    later = {**DEVICE, "app_version": "0.3.1", "plugins": []}
    assert (await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "device": later})).status_code == 200
    other = _headers(_agent_token(did="device-8"))
    assert (await client.put(MANIFEST_URL, headers=other, json={**MANIFEST, "device": DEVICE})).status_code == 200
    rows = {r.device_id: r for r in await _devices(db_session)}
    assert set(rows) == {"device-7", "device-8"}
    assert (rows["device-7"].app_version, rows["device-7"].plugins) == ("0.3.1", [])
    assert rows["device-7"].reported_at >= first


@pytest.mark.anyio
async def test_a_token_without_a_device_keeps_no_device_block(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, manifests: None,  # noqa: F811
) -> None:
    no_device = _headers(_agent_token(did=None))
    r = await client.put(MANIFEST_URL, headers=no_device, json={**MANIFEST, "device": DEVICE})
    assert r.status_code == 200, r.text
    assert await _devices(db_session) == []


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [
    {"plugins": [{"name": "x", "sha256": "A" * 64}]},  # not lowercase hex
    {"plugins": [{"name": "x", "sha256": SHA[:63]}]},
    {"plugins": [{"name": "x"}]},  # no hash
    {"mcp_programs": [{"name": "", "sha256": SHA}]},
    {"os": "x" * 101},
    {"app_version": "1" * 51},
    {"plugins": [{"name": f"p{i}", "sha256": SHA} for i in range(101)]},
    {"mcp_programs": [{"name": "m", "program": "p" * 256, "sha256": SHA}]},
], ids=["uppercase", "short", "no-hash", "no-name", "long-os", "long-version", "too-many", "long-program"])
async def test_a_device_block_is_checked(
    bad: dict[str, Any], client: AsyncClient, db_session: AsyncSession, backend: _Backend,  # noqa: F811
    manifests: None,  # noqa: F811
) -> None:
    r = await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "device": bad})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_manifest"
    assert await _devices(db_session) == []


@pytest.mark.anyio
async def test_unknown_fields_of_a_device_block_are_dropped(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, manifests: None,  # noqa: F811
) -> None:
    sent = {**DEVICE, "hostname": "ALICE-LAPTOP", "plugins": [{"name": "p", "sha256": SHA, "path": "C:\\x"}]}
    assert (await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "device": sent})).status_code == 200
    [row] = await _devices(db_session)
    assert "ALICE" not in json.dumps([row.os, row.plugins, row.mcp_programs])
    assert row.plugins == [{"name": "p", "sha256": SHA}]


def _row(device_id: str, user_id: str, reported_at: datetime) -> AgentDeviceInventory:
    return AgentDeviceInventory(
        agent_id=AGENT_ID, device_id=device_id, tenant_id="tenant-a", user_id=user_id, os=None, app_version=None,
        plugins=[], mcp_programs=[], reported_at=reported_at,
    )


@pytest.mark.anyio
async def test_a_device_block_goes_with_the_account_and_after_ninety_days(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    db_session.add_all([
        _row("mine", USER, now), _row("theirs", "someone-else", now),
        _row("old", "someone-else", now - timedelta(days=91)),
        _row("recent", "someone-else", now - timedelta(days=89)),
    ])
    await db_session.flush()
    await erasure.erase_user(db_session, None, USER)
    assert {r.device_id for r in await _devices(db_session)} == {"theirs", "old", "recent"}
    assert await agent_manifests.purge_devices(db_session, now=now) == 1
    assert {r.device_id for r in await _devices(db_session)} == {"theirs", "recent"}


# -- the arguments' hash on the device's decision records ----------------------------------------------


@pytest.mark.anyio
async def test_the_device_records_carry_the_arguments_hash_as_sent(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,  # noqa: F811
) -> None:
    body = {"events": [
        {"type": "approval.decided", "tool": "delete_page", "decision": "approved", "scope": "once",
         "arguments_sha256": SHA},
        {"type": "tool.executed", "tool": "delete_page", "duration_ms": 12, "outcome": "ok",
         "arguments_sha256": SHA},
        {"type": "tool.executed", "tool": "read_page", "duration_ms": 3, "outcome": "ok"},
    ]}
    r = await client.post("/v1/agents/eplan-assistant/events", headers=_headers(), json=body)
    assert r.status_code == 202, r.text
    rows = (await db_session.execute(select(AgentClientEvent))).scalars().all()
    by_tool = {(e.type, e.data["tool"]): e.data for e in rows}
    assert by_tool[("approval.decided", "delete_page")]["arguments_sha256"] == SHA
    assert by_tool[("tool.executed", "delete_page")]["arguments_sha256"] == SHA
    assert "arguments_sha256" not in by_tool[("tool.executed", "read_page")], "none sent: the shape as before"


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["A" * 64, SHA[:63], SHA + "a", "g" * 64, 7])
async def test_an_arguments_hash_that_is_no_sha256_is_refused(
    value: Any, client: AsyncClient, db_session: AsyncSession, backend: _Backend,  # noqa: F811
) -> None:
    for event in (
        {"type": "approval.decided", "tool": "x", "decision": "approved", "arguments_sha256": value},
        {"type": "tool.executed", "tool": "x", "duration_ms": 1, "outcome": "ok", "arguments_sha256": value},
    ):
        r = await client.post("/v1/agents/eplan-assistant/events", headers=_headers(), json={"events": [event]})
        assert r.status_code == 400, (event, r.text)
        assert r.json()["error"]["code"] == "invalid_events"
    assert (await db_session.execute(select(AgentClientEvent))).scalars().all() == []


def test_the_device_block_is_its_own_not_the_stored_manifests() -> None:
    manifest = agent_manifests.AppManifest.model_validate({**MANIFEST, "device": DEVICE})
    assert manifest.device is not None and "device" not in manifest.stored()
    assert agent_manifests.file_name("C:\\a\\b/c.exe") == "c.exe"
    assert agent_manifests.file_name("plain") == "plain"
