"""What the apps that run an agent bring, as they say: kept for review, never applied.

An app built with an SDK sends its manifest when it starts (``PUT /v1/agents/{agent}/manifest``): its host
tools with the description, parameters and class their developer gave them, the context it offers, its
default greeting and suggestions, and the app's and the SDK's versions. Tools the gateway holds back from a
request, because the agent's contract does not list them, are kept the same way: what the request declared,
without a class. The console compares both with the agent's contract, so an app update that brings tools
shows up there and an admin takes them in with one click.

Nothing here changes what an agent may do: its contract does, and only its maintainers change that.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AgentClientManifest

TOOL_NAME = r"^[A-Za-z0-9_.:-]{1,128}$"
#: A tool's parameter schema, and a whole manifest, at most.
MAX_SCHEMA_BYTES = 32_000
MAX_MANIFEST_BYTES = 512_000
#: Distinct manifests an agent keeps; beyond that new ones are refused, so nobody fills the table.
MAX_MANIFESTS_PER_AGENT = 500
#: A request's held-back tools are written once in this long per agent and tool set.
HELD_RECORD_INTERVAL_S = 600.0

_TOOL_NAME = re.compile(TOOL_NAME)


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spaces: the same value always gives the same text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(value: Any) -> str:
    """SHA-256 of the canonical JSON, in hex."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ManifestTool(BaseModel):
    """A host tool as the app's developer describes it."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(pattern=TOOL_NAME)
    description: str | None = Field(default=None, max_length=4000)
    parameters: dict[str, Any] | None = None
    #: The class the app gives it -- a suggestion until a maintainer accepts it into the contract.
    tool_class: Literal["read", "write", "destructive", "unknown"] = Field(default="unknown", alias="class")
    open_world: bool | None = None

    @field_validator("parameters")
    @classmethod
    def _small(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(canonical(value).encode()) > MAX_SCHEMA_BYTES:
            raise ValueError(f"a tool's parameters may be {MAX_SCHEMA_BYTES} bytes at most")
        return value


class ManifestContext(BaseModel):
    """Something the app offers the agent to read, by its id (``page:document``, ``doc:selection``)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=64)


class ManifestUi(BaseModel):
    """The greeting and suggestions the app would show by default."""

    model_config = ConfigDict(extra="ignore")

    greeting: str | None = Field(default=None, max_length=1000)
    suggestions: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=10)


class ManifestApp(BaseModel):
    """The app: its name and version."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(default="", max_length=50)


class ManifestSdk(BaseModel):
    """The SDK the app is built with."""

    model_config = ConfigDict(extra="ignore")

    language: str = Field(min_length=1, max_length=32)
    version: str = Field(default="", max_length=32)


class AppManifest(BaseModel):
    """What an app brings to the agent it runs. Unknown fields are dropped (a newer SDK may send more)."""

    model_config = ConfigDict(extra="ignore")

    app: ManifestApp
    sdk: ManifestSdk | None = None
    tools: list[ManifestTool] = Field(default_factory=list, max_length=200)
    context: list[ManifestContext] = Field(default_factory=list, max_length=100)
    ui: ManifestUi | None = None

    @field_validator("tools")
    @classmethod
    def _unique(cls, tools: list[ManifestTool]) -> list[ManifestTool]:
        names = [t.name for t in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return tools

    def stored(self) -> dict[str, Any]:
        """The manifest as kept: tools and context in name order, each schema with its hash."""
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        data["tools"] = [_with_schema_hash(t) for t in sorted(data.get("tools", []), key=lambda t: t["name"])]
        data["context"] = sorted(data.get("context", []), key=lambda c: c["id"])
        return data


def _with_schema_hash(tool: dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool.get("parameters"), dict):
        return {**tool, "schema_sha256": sha256_of(tool["parameters"])}
    return tool


def held_manifest(tools: list[Any]) -> dict[str, Any]:
    """Tools held back from a request, as a manifest: what the request declared, without a class or an app."""
    kept: list[dict[str, Any]] = []
    for tool in tools:
        function = (tool.get("function") or {}) if isinstance(tool, dict) else {}
        name = function.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.match(name):
            continue
        entry: dict[str, Any] = {"name": name, "class": "unknown"}
        if isinstance(function.get("description"), str):
            entry["description"] = function["description"][:4000]
        parameters = function.get("parameters")
        if isinstance(parameters, dict) and len(canonical(parameters).encode()) <= MAX_SCHEMA_BYTES:
            entry["parameters"] = parameters
        kept.append(_with_schema_hash(entry))
    unique = {t["name"]: t for t in kept}
    return {"app": {"name": "", "version": ""}, "tools": [unique[n] for n in sorted(unique)], "context": []}


class ManifestLimitError(Exception):
    """The agent keeps as many manifests as it may."""


_recent: dict[tuple[str, str], float] = {}


def forget_recent() -> None:
    """Forgets which manifests were written lately (tests)."""
    _recent.clear()


def recently_recorded(agent_id: str, digest: str, *, now: float | None = None) -> bool:
    """Whether this agent's manifest was written within the interval; marks it written if not."""
    moment = time.monotonic() if now is None else now
    if len(_recent) > 10_000:
        for key in [k for k, at in _recent.items() if moment - at > HELD_RECORD_INTERVAL_S]:
            del _recent[key]
    at = _recent.get((agent_id, digest))
    if at is not None and moment - at < HELD_RECORD_INTERVAL_S:
        return True
    _recent[(agent_id, digest)] = moment
    return False


async def _find(session: AsyncSession, agent_id: str, digest: str) -> AgentClientManifest | None:
    found = await session.execute(
        select(AgentClientManifest).where(
            AgentClientManifest.agent_id == agent_id, AgentClientManifest.sha256 == digest,
        ),
    )
    return found.scalar_one_or_none()


async def record(
    session: AsyncSession,
    *,
    tenant_id: str,
    agent_id: str,
    user_id: str,
    device_id: str | None,
    manifest: dict[str, Any],
    source: Literal["app", "request"],
) -> tuple[str, bool]:
    """Keeps *manifest* for the agent: a new row, or the known one counted again. ``(sha256, new)``."""
    digest = sha256_of(manifest)
    stamp = datetime.now(UTC)
    row = await _find(session, agent_id, digest)
    if row is None:
        count = await session.execute(
            select(func.count())
            .select_from(AgentClientManifest)
            .where(AgentClientManifest.agent_id == agent_id),
        )
        if count.scalar_one() >= MAX_MANIFESTS_PER_AGENT:
            raise ManifestLimitError(f"The agent keeps {MAX_MANIFESTS_PER_AGENT} manifests at most.")
        app = manifest.get("app") or {}
        session.add(AgentClientManifest(
            tenant_id=tenant_id,
            agent_id=agent_id,
            sha256=digest,
            source=source,
            app_name=str(app.get("name") or ""),
            app_version=str(app.get("version") or ""),
            manifest=manifest,
            first_seen_at=stamp,
            last_seen_at=stamp,
            reports=1,
            last_user_id=user_id,
            last_device_id=device_id,
        ))
        try:
            await session.commit()
            return digest, True
        except IntegrityError:  # another device sent the same one at the same moment
            await session.rollback()
            row = await _find(session, agent_id, digest)
            if row is None:
                raise
    row.last_seen_at = stamp
    row.reports += 1
    row.last_user_id = user_id
    row.last_device_id = device_id
    await session.commit()
    return digest, False
