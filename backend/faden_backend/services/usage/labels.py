"""Names for the keys of usage figures: teams and agents from the backend, providers through attribution.

The rollups hold ids only (S01: usage is keyed by internal ids; names are resolved for display). A model is
named by its alias. A chat project's name is sealed in the gateway with the chat's content, so projects get no
label here: the console names a person's own projects from the chat it already has.
"""

from __future__ import annotations

import uuid
from collections.abc import Hashable, Iterable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.agents import Agent
from faden_backend.db.models.groups import Group
from faden_backend.db.models.inference import InferenceDeployment, InferenceEnvironment
from faden_backend.db.models.llm import LLMProvider, LLMRuntime
from faden_backend.services.dashboard.traffic import Attribution


def _uuids(keys: Iterable[Hashable]) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    for key in keys:
        try:
            found.append(uuid.UUID(str(key)))
        except ValueError:
            continue
    return found


async def _names(
    session: AsyncSession, model: type[Group | Agent], keys: Iterable[Hashable],
) -> dict[str, str]:
    ids = _uuids(keys)
    if not ids:
        return {}
    rows = await session.execute(select(model.id, model.name).where(model.id.in_(ids)))
    return {str(row_id): name for row_id, name in rows}


async def _providers(session: AsyncSession, gw: AsyncSession, keys: Iterable[Hashable]) -> dict[str, str]:
    """A provider instance's destination name: its cluster, its provider (as the dashboard names them)."""
    ids = _uuids(keys)
    if not ids:
        return {}
    sources = await gw.execute(
        text(
            "SELECT CAST(id AS TEXT) AS id, source_kind, source_id FROM llm_provider_instance "
            "WHERE id = ANY(:ids)",
        ),
        {"ids": ids},
    )
    attribution = Attribution(
        providers=list((await session.execute(select(LLMProvider))).scalars()),
        runtimes=list((await session.execute(select(LLMRuntime))).scalars()),
        deployments={
            str(d_id): str(env_id)
            for d_id, env_id in await session.execute(
                select(InferenceDeployment.id, InferenceDeployment.environment_id),
            )
        },
        environments={
            str(e_id): name
            for e_id, name in await session.execute(
                select(InferenceEnvironment.id, InferenceEnvironment.name),
            )
        },
        residency={},  # names only: no residency classification (it may resolve addresses)
    )
    names: dict[str, str] = {}
    for row in sources.mappings():
        destination = attribution.destination(row["id"], row["source_kind"], row["source_id"])
        if destination.name:
            names[row["id"]] = destination.name
    return names


async def label(
    session: AsyncSession, gw: AsyncSession, dimension: str, keys: Iterable[Hashable],
) -> dict[Hashable, str | None]:
    """A display name per key of *dimension* (``team``, ``agent``, ``model``, ``provider``, ``project``)."""
    wanted = [key for key in keys if key is not None]
    if dimension == "team":
        names = await _names(session, Group, wanted)
    elif dimension == "agent":
        names = await _names(session, Agent, wanted)
    elif dimension == "provider":
        names = await _providers(session, gw, wanted)
    elif dimension == "model":
        names = {str(key): str(key) for key in wanted}
    else:
        names = {}
    return {key: names.get(str(key)) for key in wanted}
