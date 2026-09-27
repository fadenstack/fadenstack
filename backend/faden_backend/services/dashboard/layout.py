"""A user's dashboard layout, saved with a revision the server assigns.

The layout follows its user to every browser and device, so two of them can
edit it at once. Deciding which edit wins by the clients' clocks lets a laptop
whose clock runs slow lose every save; so the server numbers the saves. A save
names the revision it was based on and is refused (409) when that is no longer
the stored one; the refusal carries the stored layout for the client to adopt.

It lives in ``user_preference.preferences["dashboard"]`` -- no table of its
own -- as ``{"layout": {...}, "revision": n}``. A save locks the user's row, so
two saves in flight are ordered and the second sees the first's revision. The
generic preferences PATCH refuses the ``dashboard`` key: this is its only writer.

Widget ids are checked for shape, not against a list: a widget added to the
frontend needs no backend change to be placed, hidden or resized.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.user_preferences import UserPreference

PREFERENCES_KEY = "dashboard"

WidgetId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
KpiId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
WidgetSize = Literal["S", "M", "L", "XL"]

#: More than any dashboard will have; bounds what one user can store.
MAX_WIDGETS = 64
MAX_KPIS = 6


class DashboardLayout(BaseModel):
    """What the user arranged: order, what is hidden, sizes, and view choices."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    order: list[WidgetId] = Field(default_factory=list, max_length=MAX_WIDGETS)
    hidden: list[WidgetId] = Field(default_factory=list, max_length=MAX_WIDGETS)
    sizes: dict[WidgetId, WidgetSize] = Field(default_factory=dict, max_length=MAX_WIDGETS)
    kpis: list[KpiId] = Field(default_factory=list, max_length=MAX_KPIS)
    traffic_window: Literal["today", "24h", "7d", "30d"] = "today"
    usage_range: Literal["7d", "30d"] = "7d"
    fleet_color_by: Literal["health", "gpu_util", "vram_allocated"] = "health"

    @field_validator("order", "hidden", "kpis")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("ids must be unique")
        return value


class StoredLayout(BaseModel):
    """The layout as stored, with its revision; ``layout`` is None before the first save."""

    layout: DashboardLayout | None = None
    revision: int = 0


def read_stored(preferences: dict[str, Any] | None) -> StoredLayout:
    """The stored layout from a preferences dict; an unreadable one counts as none.

    Its revision is kept even then, so the next save still has to be based on it.
    """
    raw = (preferences or {}).get(PREFERENCES_KEY)
    if not isinstance(raw, dict):
        return StoredLayout()
    revision = raw.get("revision")
    revision = revision if isinstance(revision, int) and revision >= 0 else 0
    try:
        layout = DashboardLayout.model_validate(raw.get("layout")) if raw.get("layout") is not None else None
    except ValidationError:
        layout = None
    return StoredLayout(layout=layout, revision=revision)


async def load(session: AsyncSession, user_id: uuid.UUID) -> StoredLayout:
    """The user's stored layout and revision."""
    prefs = (
        await session.execute(select(UserPreference.preferences).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()
    return read_stored(prefs)


async def save(
    session: AsyncSession,
    user_id: uuid.UUID,
    layout: DashboardLayout,
    base_revision: int,
) -> tuple[bool, StoredLayout]:
    """Store *layout* if *base_revision* is the stored revision, and commit.

    Returns ``(True, new)`` on success, ``(False, current)`` when another save
    got there first.
    """
    # Make sure the row exists without racing a concurrent first save: the
    # insert is a no-op when it does, and the lock below orders the rest.
    await session.execute(
        insert(UserPreference)
        .values(id=uuid.uuid4(), user_id=user_id, preferences={})
        .on_conflict_do_nothing(index_elements=[UserPreference.user_id]),
    )
    pref = (
        await session.execute(
            select(UserPreference).where(UserPreference.user_id == user_id).with_for_update(),
        )
    ).scalar_one()
    current = read_stored(pref.preferences)
    if current.revision != base_revision:
        await session.rollback()
        return False, current

    stored = StoredLayout(layout=layout, revision=current.revision + 1)
    # A new dict, so SQLAlchemy sees the JSONB column change.
    pref.preferences = {**(pref.preferences or {}), PREFERENCES_KEY: stored.model_dump(mode="json")}
    await session.commit()
    return True, stored
