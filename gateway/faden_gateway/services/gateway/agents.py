"""Agents, as the gateway sees them: who may use one, and what it applies.

The backend owns agents -- versions, grants, maintainers -- and issues the
tokens. The gateway reads them from the backend database, as it reads a
user's groups (``usage_group.py``): one query per agent, cached in-process for
a minute, concurrent misses for one agent sharing the query. A request pays
for the lookup only when the cache is cold.

Unlike the usage-group lookup, a failed read fails the request (503): an
agent that cannot be looked up cannot be allowed.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import asyncpg

from faden_gateway.services.extensions import ExtensionRegistry
from faden_gateway.services.extensions import registry as default_registry

logger = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")

#: The agent with the version it serves: the published one, or -- while it
#: has none -- the newest draft, which only its maintainers may use.
_AGENT_SQL = """
SELECT a.id::text AS id, a.tenant_id, a.slug, a.name, a.status::text AS status,
       (a.published_version_id IS NOT NULL) AS published,
       v.version, v.instructions, v.model_alias, v.knowledge, v.server_tools,
       v.host_tool_contract, v.local_mcp_mode::text AS local_mcp_mode, v.ui, v.pii, v.extensions,
       ARRAY(SELECT m.user_id::text FROM agent_members m
             WHERE m.agent_id = a.id AND m.role = 'maintainer') AS maintainers,
       ARRAY(SELECT g.principal_id::text FROM agent_grants g
             WHERE g.agent_id = a.id AND g.principal_type = 'user') AS grant_users,
       ARRAY(SELECT g.principal_id::text FROM agent_grants g
             WHERE g.agent_id = a.id AND g.principal_type = 'group') AS grant_groups
FROM agents a
LEFT JOIN LATERAL (
    SELECT * FROM agent_versions v
    WHERE v.id = a.published_version_id
       OR (a.published_version_id IS NULL AND v.agent_id = a.id)
    ORDER BY v.version DESC
    LIMIT 1
) v ON true
WHERE a.tenant_id = $1 AND (a.slug = $2 OR a.id::text = $2)
"""

_USER_SQL = 'SELECT is_active, is_superuser FROM "user" WHERE id = $1::uuid'


def _json(value: Any, default: Any) -> Any:
    """A JSONB value as asyncpg returns it (text), decoded."""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _tri_state(settings: Any, key: str) -> bool | None:
    """``settings[key]`` when it is a boolean; anything else is unset (``None``)."""
    value = settings.get(key) if isinstance(settings, dict) else None
    return value if isinstance(value, bool) else None


@dataclass(frozen=True)
class AgentProfile:
    """An agent and the version it serves."""

    id: str
    tenant_id: str
    slug: str
    name: str
    status: str
    published: bool
    version: int | None
    instructions: str
    model_alias: str | None
    knowledge: tuple[str, ...]
    server_tools: tuple[str, ...] | None
    host_tool_contract: tuple[dict[str, Any], ...]
    local_mcp_mode: str
    ui: dict[str, Any]
    extensions: dict[str, Any]
    maintainers: frozenset[str]
    grant_users: frozenset[str]
    grant_groups: frozenset[str]
    instructions_sha256: str = ""
    #: Whether the agent's conversations may switch the PII mode (``pii.allow_mode_override``
    #: of the version); ``None`` follows the tenant's setting.
    pii_allow_mode_override: bool | None = None

    @property
    def host_tools(self) -> frozenset[str]:
        """The names of the tools the host application may declare."""
        return frozenset(str(t.get("name", "")) for t in self.host_tool_contract)

    @classmethod
    def from_row(cls, row: Any) -> AgentProfile:
        instructions = row["instructions"] or ""
        server_tools = _json(row["server_tools"], None)
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            slug=row["slug"],
            name=row["name"],
            status=row["status"],
            published=bool(row["published"]),
            version=row["version"],
            instructions=instructions,
            model_alias=row["model_alias"],
            knowledge=tuple(_json(row["knowledge"], [])),
            server_tools=tuple(server_tools) if server_tools is not None else None,
            host_tool_contract=tuple(_json(row["host_tool_contract"], [])),
            local_mcp_mode=row["local_mcp_mode"] or "disable",
            ui=_json(row["ui"], {}),
            extensions=_json(row["extensions"], {}),
            maintainers=frozenset(row["maintainers"] or ()),
            grant_users=frozenset(row["grant_users"] or ()),
            grant_groups=frozenset(row["grant_groups"] or ()),
            instructions_sha256=hashlib.sha256(instructions.encode()).hexdigest(),
            pii_allow_mode_override=_tri_state(_json(row["pii"], {}), "allow_mode_override"),
        )


@dataclass(frozen=True)
class UserFlags:
    """What the gateway needs to know about a user beside their token."""

    is_active: bool
    is_superuser: bool


class AgentLookupError(Exception):
    """The backend database could not be read."""


class TtlCache(Generic[K, V]):
    """``key -> value`` with a TTL in front of ``fetch``; misses coalesce.

    A failed fetch is not cached and propagates, so the request fails rather
    than being answered from a guess.
    """

    def __init__(
        self,
        fetch: Callable[[K], Awaitable[V]],
        *,
        ttl_sec: float = 60.0,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_sec
        self._max = max_entries
        self._clock = clock
        self._cache: dict[K, tuple[V, float]] = {}
        self._inflight: dict[K, asyncio.Future[V]] = {}

    async def get(self, key: K) -> V:
        hit = self._cache.get(key)
        if hit is not None and hit[1] > self._clock():
            return hit[0]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        fut: asyncio.Future[V] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await self._fetch(key)
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()  # retrieved: no "never retrieved" warning when nobody waited
            raise
        else:
            self._remember(key, value)
            fut.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)

    def invalidate(self) -> None:
        self._cache.clear()

    def forget(self, key: K) -> None:
        """Drops one key: the next ``get`` reads it again."""
        self._cache.pop(key, None)

    def _remember(self, key: K, value: V) -> None:
        if len(self._cache) >= self._max and key not in self._cache:
            now = self._clock()
            for stale in [k for k, (_, exp) in self._cache.items() if exp <= now]:
                del self._cache[stale]
            if len(self._cache) >= self._max:
                del self._cache[min(self._cache, key=lambda k: self._cache[k][1])]
        self._cache[key] = (value, self._clock() + self._ttl)


class BackendAgents:
    """Reads agents and users from the backend database over a small pool."""

    def __init__(self, dsn: str, *, max_size: int = 4, timeout_sec: float = 2.0) -> None:
        self._dsn = dsn
        self._max_size = max_size
        self._timeout = timeout_sec
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def agent(self, key: tuple[str, str]) -> AgentProfile | None:
        tenant_id, ref = key
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow(_AGENT_SQL, tenant_id, ref, timeout=self._timeout)
        except Exception as exc:
            raise AgentLookupError(str(exc)) from exc
        return AgentProfile.from_row(row) if row is not None else None

    async def user(self, user_id: str) -> UserFlags | None:
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow(_USER_SQL, user_id, timeout=self._timeout)
        except (ValueError, asyncpg.DataError):
            return None  # not a user id
        except Exception as exc:
            raise AgentLookupError(str(exc)) from exc
        return UserFlags(bool(row["is_active"]), bool(row["is_superuser"])) if row is not None else None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self._dsn, min_size=0, max_size=self._max_size, timeout=self._timeout,
                    )
        return self._pool


class AgentDirectory:
    """Cached agents and users, for the agent routes."""

    def __init__(
        self,
        *,
        fetch_agent: Callable[[tuple[str, str]], Awaitable[AgentProfile | None]],
        fetch_user: Callable[[str], Awaitable[UserFlags | None]],
        ttl_sec: float = 60.0,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._agents: TtlCache[tuple[str, str], AgentProfile | None] = TtlCache(fetch_agent, ttl_sec=ttl_sec)
        self._users: TtlCache[str, UserFlags | None] = TtlCache(fetch_user, ttl_sec=ttl_sec)
        self._close = close

    async def agent(self, tenant_id: str, ref: str, *, fresh: bool = False) -> AgentProfile | None:
        """The agent, from the cache unless *fresh* (it may have changed: deleted and made again)."""
        if fresh:
            self._agents.forget((tenant_id, ref))
        return await self._agents.get((tenant_id, ref))

    async def user(self, user_id: str) -> UserFlags | None:
        return await self._users.get(user_id)

    def invalidate(self) -> None:
        self._agents.invalidate()
        self._users.invalidate()

    async def close(self) -> None:
        if self._close is not None:
            await self._close()


def build_agent_directory(settings: object) -> AgentDirectory | None:
    """The directory over the backend database; ``None`` when none is configured."""
    backend_db = getattr(settings, "backend_db_base", "")
    if not backend_db:
        return None
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"  # type: ignore[attr-defined]
        f"@{settings.db_host}:{settings.db_port}/{backend_db}"  # type: ignore[attr-defined]
    )
    backend = BackendAgents(dsn)
    return AgentDirectory(fetch_agent=backend.agent, fetch_user=backend.user, close=backend.close)


# -- the decision -------------------------------------------------------------------


@dataclass(frozen=True)
class AgentDecision:
    """Whether a caller may use an agent; the HTTP status and code when not."""

    allowed: bool
    status_code: int = 200
    code: str = ""
    message: str = ""
    maintainer: bool = False


def _deny(status_code: int, code: str, message: str) -> AgentDecision:
    return AgentDecision(False, status_code, code, message)


async def decide(
    agent: AgentProfile,
    *,
    user_id: str,
    user: UserFlags | None,
    groups: tuple[str, ...],
    registry: ExtensionRegistry = default_registry,
) -> AgentDecision:
    """The backend's access rule (``services/agents/access.py``), on cached data."""
    if user is None or not user.is_active:
        return _deny(403, "user_inactive", "The user is deactivated or unknown.")
    if agent.status == "retired":
        return _deny(403, "agent_retired", "The agent is retired.")
    if user.is_superuser or user_id in agent.maintainers:
        return AgentDecision(True, maintainer=True)
    closed = _closed(agent, registry)
    if closed is not None:
        return closed
    if user_id not in agent.grant_users and not agent.grant_groups.intersection(groups):
        return _deny(403, "agent_not_granted", "You are not granted this agent.")
    return await _ruled(agent, user_id, registry)


async def _ruled(agent: AgentProfile, user_id: str, registry: ExtensionRegistry) -> AgentDecision:
    """The Enterprise access rules, each of which can still deny."""
    for rule in registry.agent_access_rules:
        reason = await rule(user_id, agent.id)
        if reason:
            return _deny(403, "agent_access_denied", reason)
    return AgentDecision(True)


def _closed(agent: AgentProfile, registry: ExtensionRegistry) -> AgentDecision | None:
    """Why *agent* is closed to all but its maintainers, if it is."""
    if not agent.published:
        return _deny(403, "agent_not_published", "The agent has no published version yet.")
    missing = registry.unenforced(agent.extensions)
    if missing:
        return _deny(
            403,
            "agent_needs_enterprise",
            "The agent uses settings that need Fadenstack Enterprise (" + ", ".join(missing) + ").",
        )
    return None


# -- what a request of an agent carries through the pipeline --------------------------------


@dataclass(frozen=True)
class AgentRequest:
    """The agent a request runs under, and the ids the request log keeps."""

    agent: AgentProfile
    device_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    developer_mode: bool = False
    maintainer: bool = False
    #: The PII egress mode the conversation asks for (``X-Faden-PII-Mode``); applied only when the
    #: agent's setting, or the tenant's, lets conversations switch it.
    pii_mode: str | None = None

    @property
    def id(self) -> str:
        return self.agent.id

    @property
    def local_mcp_allowed(self) -> bool:
        """Whether the device may offer its local MCP servers' tools.

        The agent allows local MCP, or the user has developer mode.
        """
        return self.agent.local_mcp_mode == "allow" or self.developer_mode

    def allows_server_tool(self, name: str) -> bool:
        """Whether the agent lets the model be offered the gateway tool *name*."""
        allowed = self.agent.server_tools
        if allowed is None:
            return True
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in allowed)
