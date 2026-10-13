"""User preferences endpoints — profile selection, tour progress and the user's language."""

from __future__ import annotations

import copy
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.user_preferences import UserPreference
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.dashboard.layout import PREFERENCES_KEY as DASHBOARD_PREFERENCES_KEY
from faden_backend.web.api.admin.users.schema import (
    UserPreferencesRead,
    UserPreferencesUpdate,
)

router = APIRouter()


async def _get_or_create(session: AsyncSession, user_id) -> UserPreference:
    """Return existing preferences row or create a default one."""
    stmt = select(UserPreference).where(UserPreference.user_id == user_id)
    result = await session.execute(stmt)
    pref = result.scalar_one_or_none()
    if pref is not None:
        return pref
    pref = UserPreference(user_id=user_id, preferences={})
    session.add(pref)
    await session.flush()
    return pref


#: The language the user picked, for the console and every agent of theirs (the gateway serves it to SDKs).
LANGUAGE_KEY = "language"

#: A BCP 47 language tag, as the console and the SDKs write them: "de", "de-AT", "zh-Hans".
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}$")


def _language_error(patch: dict) -> str | None:
    """Why the language in *patch* cannot be kept; ``None`` when it can, is removed, or is not there."""
    if LANGUAGE_KEY not in patch or patch[LANGUAGE_KEY] is None:
        return None
    value = patch[LANGUAGE_KEY]
    if isinstance(value, str) and _LANGUAGE_TAG.match(value):
        return None
    return "language must be a language tag such as de, de-AT or zh-Hans, or null."


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge *patch* into *base*, returning a new dict.

    A ``None`` in *patch* removes the key: an empty dict would merge into the
    stored one and leave it as it was, so "Reset guides" never reset anything.
    """
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
            continue
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@router.get("/me/preferences", response_model=UserPreferencesRead, name="me_preferences")
async def get_preferences(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> UserPreferencesRead:
    """Return current user's preferences (auto-creates a default row)."""
    pref = await _get_or_create(session, user.id)
    await session.commit()
    return UserPreferencesRead(preferences=pref.preferences)


@router.patch("/me/preferences", response_model=UserPreferencesRead, name="update_me_preferences")
async def update_preferences(
    payload: UserPreferencesUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> UserPreferencesRead:
    """Merge-update the current user's preferences."""
    if DASHBOARD_PREFERENCES_KEY in payload.preferences:
        # The dashboard layout carries a revision that only its own endpoint
        # keeps; a merge here would overwrite it without checking it.
        raise HTTPException(
            status_code=422,
            detail="The dashboard layout is saved through /api/admin/dashboard/layout.",
        )
    language_error = _language_error(payload.preferences)
    if language_error is not None:
        raise HTTPException(status_code=422, detail=language_error)
    pref = await _get_or_create(session, user.id)
    pref.preferences = _deep_merge(pref.preferences, payload.preferences)
    await session.commit()
    await session.refresh(pref)
    return UserPreferencesRead(preferences=pref.preferences)
