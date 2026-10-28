"""The ``usage.*`` settings: read live at each request, checked against the registry when changed.

They are stored like every system setting (``system_setting_value``) and defined in the settings registry
(``services/system_settings/registry.py``, ``USAGE_SETTINGS``), which holds their defaults and ranges. The
gateway's rollup job reads the same rows at each run, so a change applies without a restart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.system_settings import SystemSettingValue
from faden_backend.services.system_settings.registry import (
    OutOfRangeError,
    SettingDefinition,
    registry_by_key,
    validate_value,
)

PREFIX = "usage."
#: The settings as the usage API names them: the registry key without ``usage.``.
NAMES = (
    "person_reporting",
    "min_group_size",
    "person_granularity",
    "request_log_retention_days",
    "person_retention_days",
    "hourly_retention_days",
    "daily_retention_months",
    "time_zone",
    "privacy_notice_url",
)
#: The smallest k there is, whatever is stored (M04: raisable, never lower).
K_FLOOR = 5


@dataclass(frozen=True)
class UsageSettings:
    """The effective ``usage.*`` settings."""

    person_reporting: bool
    min_group_size: int
    person_granularity: str
    request_log_retention_days: int
    person_retention_days: int
    hourly_retention_days: int
    daily_retention_months: int
    time_zone: str
    privacy_notice_url: str

    @property
    def k(self) -> int:
        """The minimum group size: never below ``K_FLOOR``, even if something wrote less into the table."""
        return max(K_FLOOR, self.min_group_size)

    @property
    def zone(self) -> ZoneInfo:
        """The time zone days and months are counted in."""
        return ZoneInfo(self.time_zone)

    def as_dict(self) -> dict[str, Any]:
        """The settings by their API names (``k`` floored)."""
        return {**asdict(self), "min_group_size": self.k}


def definitions() -> dict[str, SettingDefinition]:
    """The registry definition of each setting, by API name."""
    by_key = registry_by_key()
    return {name: by_key[PREFIX + name] for name in NAMES}


def _effective(defn: SettingDefinition, stored: object) -> object:
    """*stored* if it is still a valid value of *defn*, else the default (a value written past the checks)."""
    try:
        return validate_value(defn, stored)
    except ValueError:
        return defn.default


async def read(session: AsyncSession) -> UsageSettings:
    """The effective settings, from the backend database."""
    defs = definitions()
    rows = await session.execute(
        select(SystemSettingValue.key, SystemSettingValue.value_json).where(
            SystemSettingValue.key.in_([PREFIX + name for name in NAMES]),
        ),
    )
    stored = {
        key.removeprefix(PREFIX): value.get("value") if isinstance(value, dict) else None
        for key, value in rows
    }
    values = {
        name: _effective(defn, stored[name]) if name in stored else defn.default
        for name, defn in defs.items()
    }
    return UsageSettings(**values)  # type: ignore[arg-type]


def check_changes(body: dict[str, Any]) -> tuple[dict[str, object], list[dict[str, str]]]:
    """The valid changes in *body* (API names), and a problem per field that is not one.

    Codes: ``invalid`` (unknown setting, wrong type, not a time zone), ``out_of_range`` (a number outside the
    setting's range).
    """
    defs = definitions()
    changes: dict[str, object] = {}
    problems: list[dict[str, str]] = []
    for name, value in body.items():
        defn = defs.get(name)
        if defn is None:
            problems.append({"field": name, "code": "invalid"})
            continue
        try:
            changes[name] = validate_value(defn, value)
        except OutOfRangeError:
            problems.append({"field": name, "code": "out_of_range"})
        except ValueError:
            problems.append({"field": name, "code": "invalid"})
    return changes, problems
