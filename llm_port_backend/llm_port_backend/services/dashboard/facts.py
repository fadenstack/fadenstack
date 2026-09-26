"""Cached access to the figures dashboard endpoints are built from.

One ``Facts`` is made per request from the app's session factories; each
figure is computed through the fact cache (``cache.py``) with sessions of its
own, never the request's, because a cached computation can outlive the
request that started it and serve another.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.dashboard import traffic
from llm_port_backend.services.dashboard.cache import FactCache
from llm_port_backend.services.dashboard.timewindows import Window

#: How long each kind of figure is reused.
TTL_ATTRIBUTION = 30.0
TTL_TOTALS = 20.0
TTL_WIDE = 60.0

#: Figures are computed for a "now" floored to this many seconds, so windows
#: that end now stay the same cache key between polls a few seconds apart.
NOW_GRANULARITY_S = 10


class GatewayUnavailableError(RuntimeError):
    """The backend has no connection to the gateway's database."""


def floored_now() -> datetime:
    """Now, floored to ``NOW_GRANULARITY_S`` seconds."""
    now = datetime.now(tz=UTC)
    return now.replace(second=now.second - now.second % NOW_GRANULARITY_S, microsecond=0)


class Facts:
    """The dashboard's figures for one request."""

    def __init__(
        self,
        *,
        cache: FactCache,
        backend_factory: Callable[[], AsyncSession] | None,
        gateway_factory: Callable[[], AsyncSession] | None,
        now: datetime,
        tz: ZoneInfo,
        tenant: str = "default",
    ) -> None:
        self.cache = cache
        self._backend = backend_factory
        self._gateway = gateway_factory
        self.now = now
        self.tz = tz
        # Figures are the platform's, not the caller's: the key holds no user.
        # The tenant goes in now so a multi-tenant future cannot mix them up.
        self._tenant = tenant

    @property
    def has_gateway(self) -> bool:
        """Whether the gateway database is configured."""
        return self._gateway is not None

    def _key(self, *parts: Any) -> tuple[Any, ...]:
        return (self._tenant, *parts)

    async def _on_gateway(self, query: Callable[[AsyncSession], Any]) -> Any:
        if self._gateway is None:
            raise GatewayUnavailableError("gateway database not configured")
        async with self._gateway() as session:
            return await query(session)

    async def attribution(self) -> traffic.Attribution:
        """How gateway instances map to destinations and residency (backend DB)."""

        async def compute() -> traffic.Attribution:
            if self._backend is None:
                raise RuntimeError("backend database not configured")
            async with self._backend() as session:
                return await traffic.Attribution.load(session)

        return await self.cache.get_or_compute(self._key("attribution"), TTL_ATTRIBUTION, compute)

    async def totals(self, window: Window, *, latency: bool = False) -> traffic.Totals:
        """Request, token, user and error counts for *window*."""
        return await self.cache.get_or_compute(
            self._key("totals", window.start, window.end, latency),
            TTL_TOTALS,
            lambda: self._on_gateway(lambda s: traffic.totals(s, window, latency=latency)),
        )

    async def instances(self, window: Window) -> list[traffic.InstanceRow]:
        """Routed requests per provider instance in *window*."""
        return await self.cache.get_or_compute(
            self._key("instances", window.start, window.end),
            TTL_TOTALS,
            lambda: self._on_gateway(lambda s: traffic.by_instance(s, window)),
        )

    async def spend(self, window: Window) -> list[traffic.SpendRow]:
        """Estimated cost per instance and currency in *window*."""
        return await self.cache.get_or_compute(
            self._key("spend", window.start, window.end),
            TTL_WIDE,
            lambda: self._on_gateway(lambda s: traffic.spend(s, window)),
        )

    async def day_buckets(self, window: Window) -> list[tuple[str, traffic.InstanceRow]]:
        """Routed requests per local calendar day and instance."""
        return await self.cache.get_or_compute(
            self._key("days", window.start, window.end, self.tz.key),
            TTL_WIDE,
            lambda: self._on_gateway(lambda s: traffic.day_buckets(s, window, self.tz)),
        )

    async def hour_buckets(self, window: Window) -> list[tuple[datetime, traffic.InstanceRow]]:
        """Routed requests per UTC hour and instance."""
        return await self.cache.get_or_compute(
            self._key("hours", window.start, window.end),
            TTL_WIDE,
            lambda: self._on_gateway(lambda s: traffic.hour_buckets(s, window)),
        )
