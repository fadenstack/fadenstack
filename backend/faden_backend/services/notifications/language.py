"""The language a mail to someone goes out in.

The language they picked (their preferences, ``language``: a BCP 47 tag, as the console and the SDKs keep
it), else the one their browser asked for when the mail was set off, else none: the mail service then
uses its own default. The mail service maps a tag to the languages it has (``de-AT`` is German).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.user_preferences import UserPreference

_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}$")


def _first_tag(accept_language: str | None) -> str | None:
    """The first language an ``Accept-Language`` header names, without its weight."""
    for part in (accept_language or "").split(","):
        tag = part.split(";", 1)[0].strip()
        if _TAG.match(tag):
            return tag
    return None


async def recipient_language(
    session: AsyncSession, user_id: uuid.UUID, accept_language: str | None = None,
) -> str | None:
    """The language of a mail to *user_id*: theirs, else the browser's, else ``None``."""
    preferences = await session.scalar(
        select(UserPreference.preferences).where(UserPreference.user_id == user_id),
    )
    chosen = (preferences or {}).get("language")
    if isinstance(chosen, str) and _TAG.match(chosen):
        return chosen
    return _first_tag(accept_language)
