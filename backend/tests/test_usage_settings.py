"""The usage settings, the instance's limits and the configuration statement (M04, M08, S09, S11).

The ranges hold on every route that writes a setting; the person-level switch needs Root Mode; each change is
recorded with its old and new value.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.services.system_settings.registry import OutOfRangeError, registry_by_key, validate_value
from faden_backend.services.usage import config as usage_config
from faden_backend.settings import settings
from faden_backend.web.api.admin.dependencies import get_root_mode_active
from faden_backend.web.api.admin.usage.views import root_mode_on
from tests.usage_fixtures import gateway, person, set_usage, sign_in

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

DEFAULTS = {
    "person_reporting": False,
    "min_group_size": 5,
    "person_granularity": "day",
    "request_log_retention_days": 90,
    "person_retention_days": 90,
    "hourly_retention_days": 90,
    "daily_retention_months": 25,
    "time_zone": "UTC",
    "privacy_notice_url": "",
}


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    user = await person(dbsession, f"admin-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"], superuser=True)
    sign_in(fastapi_app, user)
    fastapi_app.dependency_overrides[root_mode_on] = lambda: False
    return user


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    rows = await db.execute(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq))
    return list(rows.scalars())


# ── The registry ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [
        ("usage.min_group_size", 5, 50),
        ("usage.request_log_retention_days", 7, 365),
        ("usage.person_retention_days", 31, 400),
        ("usage.hourly_retention_days", 31, 180),
        ("usage.daily_retention_months", 13, 60),
    ],
)
def test_the_ranges_of_the_compliance_report(key: str, low: int, high: int) -> None:
    defn = registry_by_key()[key]
    assert validate_value(defn, low) == low and validate_value(defn, high) == high
    for wrong in (low - 1, high + 1):
        with pytest.raises(OutOfRangeError):
            validate_value(defn, wrong)
    with pytest.raises(ValueError, match="integer"):
        validate_value(defn, True)


def test_the_switch_is_protected_and_the_time_zone_checked() -> None:
    by_key = registry_by_key()
    assert by_key["usage.person_reporting"].protected is True
    assert validate_value(by_key["usage.time_zone"], "Europe/Berlin") == "Europe/Berlin"
    for wrong in ("Mars/Olympus", "../etc/passwd", ""):
        with pytest.raises(ValueError, match="time zone"):
            validate_value(by_key["usage.time_zone"], wrong)


async def test_k_stored_below_five_still_counts_as_five(dbsession: AsyncSession) -> None:
    """Something that writes the table past the checks cannot lower k (M04: raisable, never lower)."""
    await set_usage(dbsession, min_group_size=2, time_zone="Nowhere/Land")
    config = await usage_config.read(dbsession)
    assert config.k == 5 and config.time_zone == "UTC"


async def test_the_generic_settings_route_keeps_the_ranges_too(
    client: AsyncClient, fastapi_app: FastAPI, admin: Any,
) -> None:
    url = fastapi_app.url_path_for("system_settings_update", key="usage.min_group_size")
    response = await client.put(url, json={"value": 3, "target_host": "local"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    switch = fastapi_app.url_path_for("system_settings_update", key="usage.person_reporting")
    assert (await client.put(switch, json={"value": True, "target_host": "local"})).status_code == 403


# ── /api/admin/usage/settings ───────────────────────────────────────


async def test_the_defaults(client: AsyncClient, admin: Any) -> None:
    response = await client.get("/api/admin/usage/settings")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == DEFAULTS


@pytest.mark.parametrize(
    ("body", "problems"),
    [
        ({"min_group_size": 4}, [{"field": "min_group_size", "code": "out_of_range"}]),
        ({"min_group_size": 51, "time_zone": "Mars/Olympus"},
         [{"field": "min_group_size", "code": "out_of_range"}, {"field": "time_zone", "code": "invalid"}]),
        ({"person_retention_days": 30}, [{"field": "person_retention_days", "code": "out_of_range"}]),
        ({"person_granularity": "hour"}, [{"field": "person_granularity", "code": "invalid"}]),
        ({"privacy_notice_url": "http://intranet/privacy"}, [{"field": "privacy_notice_url", "code": "invalid"}]),
        ({"privacy_notice_url": "https://"}, [{"field": "privacy_notice_url", "code": "invalid"}]),
        ({"min_group_size": "5"}, [{"field": "min_group_size", "code": "invalid"}]),
        ({"device_tracking": True}, [{"field": "device_tracking", "code": "invalid"}]),
    ],
)
async def test_bad_values_are_named_and_nothing_is_saved(
    client: AsyncClient, dbsession: AsyncSession, admin: Any, body: dict[str, Any], problems: list[Any],
) -> None:
    response = await client.put("/api/admin/usage/settings", json={"daily_retention_months": 30, **body})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == {"problems": problems}
    assert (await usage_config.read(dbsession)).daily_retention_months == 25


async def test_a_change_is_saved_and_recorded_with_old_and_new_value(
    client: AsyncClient, dbsession: AsyncSession, admin: Any,
) -> None:
    body = {**DEFAULTS, "min_group_size": 10, "time_zone": "Europe/Berlin", "person_granularity": "month"}
    response = await client.put("/api/admin/usage/settings", json=body)
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == body
    events = await _events(dbsession, "system.settings.update")
    assert [event.target_id for event in events] == [
        "usage.min_group_size", "usage.person_granularity", "usage.time_zone",
    ]
    assert json.loads(events[0].metadata_json or "{}") == {"from": 5, "to": 10, "via": "usage"}
    assert all(event.actor_id == admin.id for event in events)


async def test_the_switch_needs_root_mode(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, admin: Any,
) -> None:
    refused = await client.put("/api/admin/usage/settings", json={"person_reporting": True})
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert refused.json()["detail"] == {"code": "root_mode"}
    assert (await usage_config.read(dbsession)).person_reporting is False

    unchanged = await client.put("/api/admin/usage/settings", json={"person_reporting": False, "min_group_size": 6})
    assert unchanged.status_code == status.HTTP_200_OK  # the switch is sent as it is: no Root Mode needed

    fastapi_app.dependency_overrides[root_mode_on] = lambda: True
    response = await client.put("/api/admin/usage/settings", json={"person_reporting": True})
    assert response.status_code == status.HTTP_200_OK and response.json()["person_reporting"] is True
    [event] = [e for e in await _events(dbsession, "system.settings.update") if e.target_id == "usage.person_reporting"]
    assert event.severity == "high"


async def test_root_mode_is_only_a_superusers(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    """An administrator who is no superuser changes the other settings, never the switch."""
    sign_in(fastapi_app, await person(dbsession, f"adm-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"]))
    assert (await client.put("/api/admin/usage/settings", json={"min_group_size": 7})).status_code == 200
    refused = await client.put("/api/admin/usage/settings", json={"person_reporting": True})
    assert refused.status_code == status.HTTP_403_FORBIDDEN


async def test_people_without_the_settings_permission_are_refused(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    sign_in(fastapi_app, await person(dbsession, f"u-{uuid.uuid4().hex[:6]}@example.com", roles=["auditor"]))
    assert (await client.get("/api/admin/usage/settings")).status_code == status.HTTP_403_FORBIDDEN
    assert (await client.put("/api/admin/usage/settings", json={"min_group_size": 7})).status_code == 403


# ── /api/admin/usage/limits ─────────────────────────────────────────


async def _policy(db: AsyncSession) -> Any:
    return (await db.execute(text("SELECT * FROM tenant_llm_policy WHERE tenant_id = 'default'"))).mappings().first()


async def test_the_limits_are_written_to_the_gateways_policy_and_recorded(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    assert (await client.get("/api/admin/usage/limits")).json() == {
        "rpm_limit": None, "tpm_limit": None, "tokens_per_day": None,
    }
    response = await client.put("/api/admin/usage/limits", json={"rpm_limit": 60, "tokens_per_day": 100000})
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {"rpm_limit": 60, "tpm_limit": None, "tokens_per_day": 100000}
    row = await _policy(gateway)
    assert (row["rpm_limit"], row["tpm_limit"], row["tokens_per_day"], row["privacy_mode"]) == (
        60, None, 100000, "metadata_only",
    )

    cleared = await client.put("/api/admin/usage/limits", json={"rpm_limit": None, "tpm_limit": 5000})
    assert cleared.json() == {"rpm_limit": None, "tpm_limit": 5000, "tokens_per_day": 100000}

    events = await _events(gateway, "usage.limits.update")
    assert len(events) == 2 and events[1].actor_id == admin.id
    assert json.loads(events[1].metadata_json or "{}") == {
        "before": {"rpm_limit": 60, "tpm_limit": None, "tokens_per_day": 100000},
        "after": {"rpm_limit": None, "tpm_limit": 5000, "tokens_per_day": 100000},
    }


async def test_saving_the_limits_keeps_the_pii_policy(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await gateway.execute(text(
        "INSERT INTO tenant_llm_policy (tenant_id, pii_config, allow_mode_override) "
        "VALUES ('default', '{\"egress\": {}}', true)",
    ))
    await client.put("/api/admin/usage/limits", json={"tpm_limit": 1000})
    row = await _policy(gateway)
    assert (row["tpm_limit"], row["pii_config"], row["allow_mode_override"]) == (1000, {"egress": {}}, True)


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ({"rpm_limit": 0}, {"field": "rpm_limit", "code": "out_of_range"}),
        ({"tokens_per_day": -5}, {"field": "tokens_per_day", "code": "out_of_range"}),
        ({"tpm_limit": 2**31}, {"field": "tpm_limit", "code": "out_of_range"}),
        ({"rpm_limit": "60"}, {"field": "rpm_limit", "code": "invalid"}),
        ({"rpm_limit": True}, {"field": "rpm_limit", "code": "invalid"}),
        ({"budget": 5}, {"field": "budget", "code": "invalid"}),
    ],
)
async def test_bad_limits_are_named(
    client: AsyncClient, gateway: AsyncSession, admin: Any, body: dict[str, Any], problem: dict[str, str],
) -> None:
    response = await client.put("/api/admin/usage/limits", json=body)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == {"problems": [problem]}
    assert await _policy(gateway) is None


async def test_the_auditor_reads_the_limits_and_cannot_change_them(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    sign_in(fastapi_app, await person(gateway, f"aud-{uuid.uuid4().hex[:6]}@example.com", roles=["auditor"]))
    assert (await client.get("/api/admin/usage/limits")).status_code == status.HTTP_200_OK
    assert (await client.put("/api/admin/usage/limits", json={"rpm_limit": 5})).status_code == 403


# ── /api/admin/usage/statement ──────────────────────────────────────


async def test_the_statement_in_english_and_german(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    holder = await person(gateway, f"works_council-{uuid.uuid4().hex[:6]}@example.com", roles=["usage_auditor"])
    await set_usage(gateway, min_group_size=10, person_retention_days=120)
    await client.put("/api/admin/usage/limits", json={"tokens_per_day": 50000})

    en = await client.get("/api/admin/usage/statement", params={"language": "en"})
    assert en.status_code == status.HTTP_200_OK
    assert en.headers["content-type"].startswith("text/markdown")
    assert en.headers["content-disposition"].endswith('-en.md"')
    assert en.text.startswith("# Usage metering: configuration statement")
    assert "| Smallest group shown | 10 people |" in en.text
    assert "| Per-person figures kept for | 120 days |" in en.text
    assert "| Tokens per day | 50000 |" in en.text and "| Requests per minute | None |" in en.text
    assert "| Usage of named people visible to usage auditors | Off |" in en.text
    assert "nobody sees it" in en.text
    assert holder.email.replace("_", "\\_") in en.text

    de = await client.get("/api/admin/usage/statement", params={"language": "de"})
    assert de.text.startswith("# Nutzungsmessung: Konfigurationsnachweis")
    assert "| Kleinste angezeigte Gruppe | 10 Personen |" in de.text
    assert "| Nutzung namentlich genannter Personen für Nutzungsprüfer sichtbar | Aus |" in de.text
    assert "keine Rechtsberatung" in de.text


async def test_the_statement_says_when_people_can_be_seen(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await set_usage(gateway, person_reporting=True)
    body = (await client.get("/api/admin/usage/statement")).text
    assert "| Usage of named people visible to usage auditors | On |" in body
    assert "each time with a stated reason" in body and "Holders of the usage auditor role: none" in body


async def test_an_unknown_language_is_refused(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    response = await client.get("/api/admin/usage/statement", params={"language": "it"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"duplicate keys: {sorted(duplicates)}"
    return dict(pairs)


@pytest.mark.parametrize("language", ["en", "de", "es", "fr", "zh"])
def test_the_bundles_have_every_statement_text_and_no_key_twice(language: str) -> None:
    """A bundle with a key twice loses one of them silently (``usage`` is shared with the console's texts)."""
    path = settings.i18n_path / language / "common.json"
    bundle = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs_without_duplicates)
    english = json.loads((settings.i18n_path / "en" / "common.json").read_text(encoding="utf-8"))
    assert set(bundle["usage"]["statement"]) == set(english["usage"]["statement"])


# ── The CLI (``faden deploy`` writes the host's time zone) ──────────


def test_the_cli_writes_the_usage_settings_but_the_switch() -> None:
    from faden_backend.tools.admin import _CLI_SETTINGS as CLI_SETTINGS
    from faden_backend.tools.admin import _cli_value as cli_value

    assert "usage.time_zone" in CLI_SETTINGS and "usage.min_group_size" in CLI_SETTINGS
    assert "usage.person_reporting" not in CLI_SETTINGS, "only in the console, in Root Mode"
    assert cli_value("usage.time_zone", "Europe/Berlin") == ("Europe/Berlin", None)
    assert cli_value("usage.min_group_size", "10") == (10, None)
    assert cli_value("usage.person_granularity", "month") == ("month", None)
    for key, raw in (("usage.time_zone", "Mars/Olympus"), ("usage.min_group_size", "3"),
                     ("usage.min_group_size", "ten"), ("chat.deleted_retention_days", "-1")):
        value, problem = cli_value(key, raw)
        assert value is None and problem, (key, raw)
    assert cli_value("chat.deleted_retention_days", "45") == (45, None)
