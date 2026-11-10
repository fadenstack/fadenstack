"""The ``approvals.*`` settings as the backend applies them (sdk/38 §5; sdk/09 L-57; sdk/40 A3).

Stored like every system setting (``system_setting_value``) and defined in the registry
(``services/system_settings/registry.py``, ``APPROVAL_SETTINGS``). The gateway reads the same rows
(``faden_gateway/services/gateway/approval_settings.py``). A value that is not valid any more counts as the
default, so Bypass stays off unless it is on.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.system_settings import SystemSettingValue
from faden_backend.services.system_settings.registry import registry_by_key, validate_value

BYPASS_ALLOWED = "approvals.bypass_allowed"


async def value(session: AsyncSession, key: str) -> object:
    """The effective value of the ``approvals.*`` setting *key*: the stored one if valid, else the default."""
    defn = registry_by_key()[key]
    stored = (await session.execute(
        select(SystemSettingValue.value_json).where(SystemSettingValue.key == key),
    )).scalar_one_or_none()
    if not isinstance(stored, dict) or "value" not in stored:
        return defn.default
    try:
        return validate_value(defn, stored["value"])
    except ValueError:
        return defn.default


async def bypass_allowed(session: AsyncSession) -> bool:
    """Whether a person may choose Bypass at all in this installation (L-57); off by default."""
    return await value(session, BYPASS_ALLOWED) is True
