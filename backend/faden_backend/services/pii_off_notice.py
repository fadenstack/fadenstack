"""Told to administrators while PII is off: requests to cloud models go out unmasked (``pii_off_cloud``).

With the PII module off and no PII policy saved for the tenant, the gateway applies nothing of the default
policy (``faden_gateway.pii_default_policy``, which asks for cloud routes to be scanned): every install has
that default, also one that never used PII, and refusing its chats would be wrong (the gateway's
``_note_default_policy_unenforced``). An install that used PII and switched it off, though, sends personal
data to cloud models unmasked from then on, and only the gateway's log said so once.

The notice holds while all of this does: the module was on once, it is off, the default asks for cloud
routes to be scanned, a cloud model is enabled, and the tenant saved no policy of its own (a saved one
refuses those requests instead, and they say why). It goes when the module is on again, or when the default
no longer asks for the scan: then sending unscanned is what an administrator chose.

The backend notes when it sees the module on (``pii.module_seen_on``): a row of ``system_setting_value``
that no setting defines, so neither the settings list nor their runtime sync reads it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.system_settings_dao import SystemSettingsDAO
from faden_backend.services.extensions import AdminNotice
from faden_backend.services.tenant_restrictions import TENANT
from faden_backend.settings import settings

#: When the backend last saw the module on (an ISO time).
SEEN_KEY = "pii.module_seen_on"
#: The module's switch as the console sets it; a stored value means it was switched, so it was on once.
SWITCH_KEY = "faden_gateway.pii_enabled"
DEFAULT_POLICY_KEY = "faden_gateway.pii_default_policy"


async def note_on(session: AsyncSession) -> None:
    """Note that the module is on now (the row's ``updated_at``); the caller commits."""
    await SystemSettingsDAO(session).upsert_value(
        key=SEEN_KEY, value_json={"value": datetime.now(UTC).isoformat()}, updated_by=None,
    )


async def notices(
    session: AsyncSession, gateway_sessions: Callable[[], AsyncSession] | None,
) -> list[AdminNotice]:
    """``pii_off_cloud`` (an error: it cannot be hidden) while cloud requests go out unmasked."""
    if settings.pii_enabled or gateway_sessions is None:
        return []
    dao = SystemSettingsDAO(session)
    seen, switch = await dao.get_value(SEEN_KEY), await dao.get_value(SWITCH_KEY)
    if seen is None and switch is None:
        return []  # never on: the default was never meant to apply
    default = await dao.get_value(DEFAULT_POLICY_KEY)
    policy = (default.value_json or {}).get("value") if default is not None else None
    if not isinstance(policy, dict) or not (policy.get("egress") or {}).get("enabled_for_cloud"):
        return []
    async with gateway_sessions() as gw:
        saved = (await gw.execute(
            text("SELECT pii_config FROM tenant_llm_policy WHERE tenant_id = :tenant"), {"tenant": TENANT},
        )).scalar_one_or_none()
        cloud = (await gw.execute(
            text("SELECT count(*) FROM llm_provider_instance WHERE enabled AND type::text LIKE 'remote%'"),
        )).scalar_one()
    if saved or not cloud:
        return []
    # When it was last known on: seen running, or its switch last changed (to off, or to on before the CLI
    # switched it off).
    last_on = max(row.updated_at for row in (seen, switch) if row is not None)
    params: dict[str, str | int] = {"date": last_on.date().isoformat()}
    return [AdminNotice(code="pii_off_cloud", severity="error", params=params)]
