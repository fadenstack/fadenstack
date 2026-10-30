"""The tenant's caps: requests and tokens per minute, tokens per calendar day (M20, S11).

Counters per tenant in Redis and nothing else: no person, no team (S11). The minute is a fixed window; the
day is a calendar day of the usage time zone (``usage.time_zone``), as LiteLLM counts its periods.

- Admission is one Lua script: every cap that is set is checked, and only an admitted request is counted.
  A refused request does not use up the window (plan decision 6): it was counted before and kept the
  window full for as long as a client retried.
- A request reserves its estimate against the token caps: its input and, for a chat, its answer
  (``max_tokens``, else 1024; ``usage.reserved_tokens``), as Azure OpenAI and LiteLLM admit. TPM counted the
  input only, never the answer. Once the answer is in, :meth:`RateLimiter.settle` replaces the reservation
  by the real input and output (the counters move by the difference and never below zero). An admitted
  stream is never cut: what it uses beyond its reservation is counted afterwards.
- A refusal is a 429 as OpenAI clients read it (C4): ``rate_limit_error`` for the minute's caps,
  ``insufficient_quota`` for the day's, with ``Retry-After`` and, when waiting longer does not help,
  ``x-should-retry: false`` (the OpenAI SDKs retry a 429 twice otherwise). The message names the kind of
  limit and whom to ask, and nothing about anyone's usage (M20).
- Every answer of a tenant with caps carries ``x-ratelimit-{limit,remaining,reset}-{requests,tokens}``,
  which clients already parse: requests from the minute's request cap, tokens from the minute's token cap,
  else the day's.

Redis keys (the backend may read them for a person's own view): ``ratelimit:rpm:<tenant>:<minute>``,
``ratelimit:tpm:<tenant>:<minute>`` (``<minute>``: Unix time // 60) and
``ratelimit:tpd:<tenant>:<YYYY-MM-DD>`` (the date in the usage time zone).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Literal

from faden_gateway.services.gateway.errors import GatewayError

if TYPE_CHECKING:
    from faden_gateway.services.cache.protocol import CacheBackend

WINDOW_SEC = 60
#: A day's counter outlives its day by this much: an answer that ends after midnight still settles on it.
DAY_GRACE_SEC = 2 * 3600

Kind = Literal["rpm", "tpm", "tokens_per_day"]
_KINDS: tuple[Kind, Kind, Kind] = ("rpm", "tpm", "tokens_per_day")

#: KEYS: the request, token and day counters. ARGV: their caps (0: none), the request's tokens, their TTLs.
#: The day is checked first: waiting for the next minute does not help a day that is used up.
_ADMIT = """
local amounts = {1, tonumber(ARGV[4]), tonumber(ARGV[4])}
local used = {}
for i = 1, 3 do
  used[i] = tonumber(redis.call('GET', KEYS[i]) or '0')
end
for _, i in ipairs({3, 1, 2}) do
  local limit = tonumber(ARGV[i])
  if limit > 0 and used[i] + amounts[i] > limit then
    return 'refused:' .. i .. ':' .. used[1] .. ':' .. used[2] .. ':' .. used[3]
  end
end
for i = 1, 3 do
  if tonumber(ARGV[i]) > 0 then
    used[i] = redis.call('INCRBY', KEYS[i], amounts[i])
    if redis.call('TTL', KEYS[i]) < 0 then
      redis.call('EXPIRE', KEYS[i], tonumber(ARGV[4 + i]))
    end
  end
end
return 'admitted:0:' .. used[1] .. ':' .. used[2] .. ':' .. used[3]
"""

#: KEYS: counters; ARGV[i]: what to add to KEYS[i]. A counter whose window is over (expired) is left
#: alone -- adding to it would make a key that never expires -- and none goes below zero.
_ADJUST = """
for i = 1, #KEYS do
  local delta = tonumber(ARGV[i])
  if delta ~= 0 and redis.call('EXISTS', KEYS[i]) == 1 then
    local value = redis.call('INCRBY', KEYS[i], delta)
    if value < 0 then
      redis.call('INCRBY', KEYS[i], -value)
    end
  end
end
return 1
"""


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


@dataclass(frozen=True, slots=True)
class Caps:
    """A tenant's caps; ``None``: no cap of that kind."""

    rpm: int | None = None
    tpm: int | None = None
    tokens_per_day: int | None = None

    @classmethod
    def of(cls, policy: Any | None) -> Caps:
        """The caps a tenant policy sets (``TenantLLMPolicy``)."""
        if policy is None:
            return cls()
        return cls(
            rpm=_positive(getattr(policy, "rpm_limit", None)),
            tpm=_positive(getattr(policy, "tpm_limit", None)),
            tokens_per_day=_positive(getattr(policy, "tokens_per_day", None)),
        )

    def limits(self) -> tuple[int, int, int]:
        """The caps in the script's order, 0 for none."""
        return (self.rpm or 0, self.tpm or 0, self.tokens_per_day or 0)

    def __bool__(self) -> bool:
        return any(self.limits())


@dataclass(slots=True)
class Reservation:
    """What an admitted request holds on its tenant's counters until its real usage is known."""

    #: Reserved against the token caps.
    tokens: int
    #: The counters it went to, by cap: the same ones are settled, though the minute may have passed.
    keys: dict[str, str]
    #: The ``x-ratelimit-*`` headers of its answer.
    headers: dict[str, str]
    settled: bool = False


@dataclass(frozen=True, slots=True)
class CapRefusal:
    """A request a cap refused; nothing was counted for it."""

    kind: Kind
    retry_after_sec: int
    headers: dict[str, str]
    #: The request alone is more than the cap allows: waiting does not help.
    too_large: bool = False
    zone_name: str = "UTC"

    def error(self) -> GatewayError:
        """The 429 as OpenAI clients read it (C4)."""
        headers = {**self.headers, "Retry-After": str(max(self.retry_after_sec, 1))}
        if self.kind == "tokens_per_day" or self.too_large:
            headers["x-should-retry"] = "false"
        return GatewayError(
            status_code=429,
            message=_message(self),
            error_type="insufficient_quota" if self.kind == "tokens_per_day" else "rate_limit_error",
            code=_CODES[self.kind],
            headers=headers,
        )


_CODES: dict[str, str] = {
    "rpm": "rate_limit_rpm",
    "tpm": "rate_limit_tpm",
    "tokens_per_day": "quota_tokens_per_day",
}

_NAMES: dict[str, str] = {
    "rpm": "this instance's limit of requests per minute",
    "tpm": "this instance's limit of tokens per minute",
    "tokens_per_day": "this instance's daily token limit",
}


def _message(refusal: CapRefusal) -> str:
    """What the refusal says (M20): the kind of limit and whom to ask, nothing about anyone's usage."""
    name = _NAMES[refusal.kind]
    if refusal.too_large:
        return (
            f"This request needs more tokens than {name} allows. Ask for a shorter answer (max_tokens), "
            "or ask an administrator to raise the limit."
        )
    if refusal.kind == "tokens_per_day":
        return (
            f"{name[0].upper()}{name[1:]} is reached; it resets at midnight ({refusal.zone_name}). "
            "Ask an administrator if you need more today."
        )
    return (
        f"{name[0].upper()}{name[1:]} is reached. Try again in {_duration(refusal.retry_after_sec)}, "
        "or ask an administrator to raise the limit."
    )


def _duration(seconds: int) -> str:
    """A wait as OpenAI's ``x-ratelimit-reset-*`` headers write it: ``12s``, ``6m0s``, ``5h3m12s``."""
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _headers(caps: Caps, used: tuple[int, int, int], minute_left: int, day_left: int) -> dict[str, str]:
    """The ``x-ratelimit-*`` headers for the counters *used*."""
    headers: dict[str, str] = {}
    if caps.rpm:
        headers["x-ratelimit-limit-requests"] = str(caps.rpm)
        headers["x-ratelimit-remaining-requests"] = str(max(caps.rpm - used[0], 0))
        headers["x-ratelimit-reset-requests"] = _duration(minute_left)
    token_cap = (caps.tpm, used[1], minute_left) if caps.tpm else (caps.tokens_per_day, used[2], day_left)
    limit, count, left = token_cap
    if limit:
        headers["x-ratelimit-limit-tokens"] = str(limit)
        headers["x-ratelimit-remaining-tokens"] = str(max(limit - count, 0))
        headers["x-ratelimit-reset-tokens"] = _duration(left)
    return headers


def _zone_name(zone: tzinfo) -> str:
    return str(getattr(zone, "key", None) or zone)


def _parse(raw: str, caps: Caps, amount: int) -> tuple[str, int, tuple[int, int, int]]:
    """The script's answer: (``admitted`` | ``refused``, the refused cap's place, the counters).

    A cache that runs no scripts (``NoOpCache``, no Redis) answers ``1``: admitted, as if the counters had
    been empty (fail-open, as the gateway runs without Redis).
    """
    parts = raw.split(":")
    if len(parts) == 5 and parts[0] in ("admitted", "refused"):
        counts = tuple(int(float(value)) for value in parts[2:])
        return parts[0], int(parts[1]), (counts[0], counts[1], counts[2])
    limits = caps.limits()
    return "admitted", 0, (1 if limits[0] else 0, amount if limits[1] else 0, amount if limits[2] else 0)


def _now() -> float:
    """The time the caps count by (a test sets its own)."""
    return time.time()


class RateLimiter:
    """The tenant's caps on its counters in the cache (Redis)."""

    def __init__(self, cache: CacheBackend, *, clock: Callable[[], float] | None = None) -> None:
        self.cache = cache
        self._clock = clock if clock is not None else _now

    async def admit(
        self, *, tenant_id: str, caps: Caps, tokens: int, zone: tzinfo,
    ) -> Reservation | CapRefusal:
        """Admit a request of *tokens* (its reservation) under *caps*, or say which cap refuses it."""
        now = self._clock()
        second = int(now)
        minute_left = max(WINDOW_SEC - second % WINDOW_SEC, 1)
        today = datetime.fromtimestamp(now, zone).date()
        midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        day_left = max(math.ceil(midnight.timestamp() - now), 1)
        keys = (
            f"ratelimit:rpm:{tenant_id}:{second // WINDOW_SEC}",
            f"ratelimit:tpm:{tenant_id}:{second // WINDOW_SEC}",
            f"ratelimit:tpd:{tenant_id}:{today.isoformat()}",
        )
        amount = max(int(tokens), 1)
        raw = await self.cache.eval_lua(
            _ADMIT, 3, *keys, *(str(limit) for limit in caps.limits()), str(amount),
            str(minute_left), str(minute_left), str(day_left + DAY_GRACE_SEC),
        )
        outcome, place, used = _parse(raw, caps, amount)
        headers = _headers(caps, used, minute_left, day_left)
        if outcome == "refused":
            kind = _KINDS[place - 1]
            limit = caps.limits()[place - 1]
            return CapRefusal(
                kind=kind,
                retry_after_sec=day_left if kind == "tokens_per_day" else minute_left,
                headers=headers,
                too_large=kind != "rpm" and amount > limit,
                zone_name=_zone_name(zone),
            )
        return Reservation(
            tokens=amount,
            keys={kind: key for kind, key, limit in zip(_KINDS, keys, caps.limits(), strict=True) if limit},
            headers=headers,
        )

    async def settle(self, reservation: Reservation, tokens: int) -> None:
        """Replace the reservation by the request's real *tokens* (input and output), once."""
        if reservation.settled:
            return
        reservation.settled = True
        await self._adjust(reservation, requests=0, tokens=max(int(tokens), 0) - reservation.tokens)

    async def release(self, reservation: Reservation) -> None:
        """Give the whole reservation back, its request too: the request was refused after all."""
        if reservation.settled:
            return
        reservation.settled = True
        await self._adjust(reservation, requests=-1, tokens=-reservation.tokens)

    async def _adjust(self, reservation: Reservation, *, requests: int, tokens: int) -> None:
        deltas = {"rpm": requests, "tpm": tokens, "tokens_per_day": tokens}
        moves = [(key, deltas[kind]) for kind, key in reservation.keys.items() if deltas[kind]]
        if moves:
            await self.cache.eval_lua(
                _ADJUST, len(moves), *(key for key, _ in moves), *(str(delta) for _, delta in moves),
            )
