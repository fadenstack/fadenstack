"""Which driver an environment runs under: its control plane's, never assumed.

Bundle resolution, hardware views and post-reconcile steps used to say
``driver="ray"`` outright. That was true while Ray was the only driver; with
``standalone`` beside it, a Mac in a standalone environment was asked for a Ray
runtime and found none.
"""

from __future__ import annotations

from typing import Any

#: What an environment without a control plane (or a test double) runs under:
#: the only driver there was before others existed.
DEFAULT_DRIVER = "ray"


async def driver_key_for(session: Any, environment: Any) -> str:
    """The driver key of *environment*'s control plane, ``ray`` when it has none."""
    from faden_backend.db.models.inference import InferenceControlPlane  # noqa: PLC0415

    cp_id = getattr(environment, "control_plane_id", None)
    if cp_id is None or session is None:
        return DEFAULT_DRIVER
    control_plane = await session.get(InferenceControlPlane, cp_id)
    return str(getattr(control_plane, "driver", None) or DEFAULT_DRIVER)


async def driver_keys_by_control_plane(session: Any) -> dict[Any, str]:
    """Every control plane's driver key, by id: one query for a list of environments."""
    from sqlalchemy import select  # noqa: PLC0415

    from faden_backend.db.models.inference import InferenceControlPlane  # noqa: PLC0415

    rows = await session.execute(select(InferenceControlPlane.id, InferenceControlPlane.driver))
    return {cp_id: str(driver or DEFAULT_DRIVER) for cp_id, driver in rows.all()}
