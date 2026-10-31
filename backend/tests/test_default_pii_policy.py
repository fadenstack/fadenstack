"""The PII policy every install starts with, and an earlier one replaced at start."""

import copy
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_backend.db.dao.system_settings_dao import SystemSettingsDAO
from faden_backend.db.models.containers import AuditEvent
from faden_backend.services.pii_default_policy import (
    CORE_DEFAULT_PII_POLICY,
    EARLIER_DEFAULTS,
    is_earlier_default,
)
from faden_backend.web.lifespan import _seed_default_settings

KEY = "faden_gateway.pii_default_policy"


def test_the_default_names_no_types_and_takes_the_detectors_thresholds() -> None:
    """None named: the PII service's standard types the installed detector finds, which every detector can serve."""
    presidio = CORE_DEFAULT_PII_POLICY["presidio"]
    assert presidio["entities"] == []
    assert presidio["threshold_mode"] == "profile"
    assert "profile_id" not in presidio


def test_local_models_are_not_scanned_by_default() -> None:
    """The default protects what leaves for cloud providers."""
    egress = CORE_DEFAULT_PII_POLICY["egress"]
    assert (egress["enabled_for_cloud"], egress["enabled_for_local"], egress["mode"]) == (True, False, "redact")
    assert egress["fail_action"] == "block"


def test_the_earlier_defaults_are_known_as_seeded() -> None:
    twelve, eleven = (set(p["presidio"]["entities"]) for p in EARLIER_DEFAULTS)
    assert twelve - eleven == {"DATE_TIME"}
    assert "NRP" in eleven
    assert all(is_earlier_default(p) for p in EARLIER_DEFAULTS)


def test_an_earlier_default_saved_again_unchanged_from_the_console_is_still_one() -> None:
    """The console's form writes the keys it knows (no profile, legacy thresholds) and may reorder the types."""
    saved = copy.deepcopy(EARLIER_DEFAULTS[1])
    saved["presidio"] |= {"profile_id": None, "threshold_mode": "legacy"}
    saved["presidio"]["entities"] = list(reversed(saved["presidio"]["entities"]))
    assert is_earlier_default(saved)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["egress"].update(mode="tokenize_reversible"),
        lambda p: p["egress"].update(fail_action="allow"),
        lambda p: p["egress"].update(enabled_for_local=True),
        lambda p: p["presidio"].update(threshold=0.5),
        lambda p: p["presidio"].update(language="de"),
        lambda p: p["presidio"]["entities"].remove("NRP"),
        lambda p: p["presidio"].update(profile_id="patronus-small-pii", threshold_mode="profile"),
        lambda p: p["telemetry"].update(enabled=True),
    ],
)
def test_a_changed_default_is_the_administrators(change: Any) -> None:
    policy = copy.deepcopy(EARLIER_DEFAULTS[1])
    change(policy)
    assert not is_earlier_default(policy)


@pytest.mark.parametrize("value", [None, {}, [], "x", {"presidio": "x"}, CORE_DEFAULT_PII_POLICY])
def test_anything_else_is_not_an_earlier_default(value: Any) -> None:
    assert not is_earlier_default(value)


def _app(dbsession: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = async_sessionmaker(dbsession.bind, expire_on_commit=False)
    return app


async def _stored(dbsession: AsyncSession) -> Any:
    row = await SystemSettingsDAO(dbsession).get_value(KEY)
    await dbsession.refresh(row)
    return row.value_json["value"]


async def _replacements(dbsession: AsyncSession) -> list[AuditEvent]:
    return list(
        (
            await dbsession.execute(
                select(AuditEvent).where(AuditEvent.target_id == KEY, AuditEvent.source == "system"),
            )
        ).scalars(),
    )


@pytest.mark.anyio
async def test_a_new_install_is_seeded_with_the_default(dbsession: AsyncSession) -> None:
    await _seed_default_settings(_app(dbsession))
    assert await _stored(dbsession) == CORE_DEFAULT_PII_POLICY


@pytest.mark.anyio
@pytest.mark.parametrize("earlier", range(len(EARLIER_DEFAULTS)))
async def test_an_earlier_default_as_seeded_is_replaced_and_recorded(dbsession: AsyncSession, earlier: int) -> None:
    await SystemSettingsDAO(dbsession).upsert_value(KEY, {"value": EARLIER_DEFAULTS[earlier]}, updated_by=None)
    await dbsession.flush()
    await _seed_default_settings(_app(dbsession))
    assert await _stored(dbsession) == CORE_DEFAULT_PII_POLICY
    assert [e.action for e in await _replacements(dbsession)] == ["system.settings.update"]
    # A second start finds the new default and leaves it.
    await _seed_default_settings(_app(dbsession))
    assert len(await _replacements(dbsession)) == 1


@pytest.mark.anyio
async def test_a_default_the_administrator_changed_stays(dbsession: AsyncSession) -> None:
    edited = copy.deepcopy(EARLIER_DEFAULTS[1])
    edited["egress"]["mode"] = "tokenize_reversible"
    await SystemSettingsDAO(dbsession).upsert_value(KEY, {"value": edited}, updated_by=None)
    await dbsession.flush()
    await _seed_default_settings(_app(dbsession))
    assert await _stored(dbsession) == edited
    assert await _replacements(dbsession) == []
