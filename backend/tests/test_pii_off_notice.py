"""PII switched off while cloud models are in use: administrators see it on top of every admin page.

With the module off and no saved policy, the gateway sends requests to cloud models unmasked and said so only
once in its log. The notice is an error (it cannot be hidden) and holds while that is what happens.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.system_settings_dao import SystemSettingsDAO
from faden_backend.services import pii_default_policy, pii_off_notice
from faden_backend.services.extensions import registry
from faden_backend.settings import settings
from tests.usage_fixtures import gateway, person, sign_in

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()


async def _notices(client: AsyncClient, fastapi_app: FastAPI) -> list[dict[str, object]]:
    response = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert response.status_code == 200, response.text
    return [n for n in response.json() if n["code"] == "pii_off_cloud"]


async def _cloud_model(db: AsyncSession, *, kind: str = "remote_openai", enabled: bool = True) -> None:
    await db.execute(
        text("INSERT INTO llm_provider_instance (id, type, enabled) VALUES (:id, :type, :enabled)"),
        {"id": uuid.uuid4(), "type": kind, "enabled": enabled},
    )


@pytest.fixture
async def pii_was_on(
    fastapi_app: FastAPI, gateway: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> AsyncSession:
    """The module was on, is off now; the seeded default policy; a superuser signed in."""
    monkeypatch.setattr(registry, "admin_notices", [])
    sign_in(fastapi_app, await person(gateway, f"a-{uuid.uuid4().hex[:6]}@example.com", superuser=True))
    dao = SystemSettingsDAO(gateway)
    await dao.upsert_value(
        key=pii_off_notice.DEFAULT_POLICY_KEY,
        value_json={"value": pii_default_policy.default_policy()},
        updated_by=None,
    )
    await pii_off_notice.note_on(gateway)
    await gateway.flush()
    monkeypatch.setattr(settings, "pii_enabled", False)
    return gateway


async def test_a_cloud_model_with_pii_switched_off_is_an_error_on_top(
    client: AsyncClient, fastapi_app: FastAPI, pii_was_on: AsyncSession,
) -> None:
    await _cloud_model(pii_was_on)
    (notice,) = await _notices(client, fastapi_app)
    assert notice["severity"] == "error", "an error: it cannot be hidden"
    assert notice["params"] == {"date": datetime.now(UTC).date().isoformat()}, "the day it was last known on"


async def test_no_notice_while_pii_is_on(
    client: AsyncClient, fastapi_app: FastAPI, pii_was_on: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _cloud_model(pii_was_on)
    monkeypatch.setattr(settings, "pii_enabled", True)
    assert await _notices(client, fastapi_app) == []


@pytest.mark.parametrize(
    "case",
    ["local models only", "cloud model switched off", "policy saved", "default scans no cloud", "never on"],
)
async def test_no_notice_when_nothing_goes_out_unmasked_by_it(
    client: AsyncClient, fastapi_app: FastAPI, pii_was_on: AsyncSession, case: str,
) -> None:
    """A saved policy refuses those requests (and they say why); a default without the scan is a choice."""
    db = pii_was_on
    if case == "local models only":
        await _cloud_model(db, kind="vllm")
    else:
        await _cloud_model(db, enabled=case != "cloud model switched off")
    if case == "policy saved":
        await db.execute(
            text("INSERT INTO tenant_llm_policy (tenant_id, pii_config) VALUES ('default', CAST(:p AS json))"),
            {"p": '{"egress": {"enabled_for_cloud": true, "fail_action": "block"}}'},
        )
    if case == "default scans no cloud":
        policy = pii_default_policy.default_policy()
        policy["egress"]["enabled_for_cloud"] = False
        await SystemSettingsDAO(db).upsert_value(
            key=pii_off_notice.DEFAULT_POLICY_KEY, value_json={"value": policy}, updated_by=None,
        )
    if case == "never on":
        await db.execute(text("DELETE FROM system_setting_value WHERE key IN (:a, :b)"),
                         {"a": pii_off_notice.SEEN_KEY, "b": pii_off_notice.SWITCH_KEY})
    await db.flush()
    assert await _notices(client, fastapi_app) == [], case


async def test_the_consoles_switch_counts_as_on_once(
    client: AsyncClient, fastapi_app: FastAPI, pii_was_on: AsyncSession,
) -> None:
    """Switched off in the console before the backend noted it on: its switch was set, so it was on."""
    db = pii_was_on
    await db.execute(text("DELETE FROM system_setting_value WHERE key = :k"), {"k": pii_off_notice.SEEN_KEY})
    await SystemSettingsDAO(db).upsert_value(
        key=pii_off_notice.SWITCH_KEY, value_json={"value": False}, updated_by=None,
    )
    await _cloud_model(db)
    await db.flush()
    assert [n["severity"] for n in await _notices(client, fastapi_app)] == ["error"]
