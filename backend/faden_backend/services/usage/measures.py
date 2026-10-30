"""The figures every usage view shows, summed from the rollups' rows.

Counting follows the OpenTelemetry GenAI conventions (the counting rules, not the metric names): input tokens
include cached ones, output tokens include reasoning ones, failed calls count. ``requests`` counts every
request that reached the gateway; ``failed`` (status 400 and up, refusals aside) and ``refused`` (by a cap or
an admission check) are parts of it, not added to it. Cost is an estimate from the
price catalogue, per currency: each rollup row holds one currency, so requests are summed over all rows and
money per currency (``{}`` when no price applies).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

#: The counted measures of every rollup table, in the order the API lists them.
COUNTS = (
    "requests",
    "failed",
    "refused",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
)

#: The measures summed in SQL, for a ``GROUP BY`` over a rollup table (with ``currency`` in the keys).
SUMS_SQL = ",\n".join(f"COALESCE(SUM({name}), 0) AS {name}" for name in COUNTS) + ",\nSUM(cost) AS cost"


@dataclass
class Measures:
    """Requests, tokens and estimated cost per currency."""

    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTS, 0))
    cost: dict[str, Decimal] = field(default_factory=dict)

    @property
    def requests(self) -> int:
        """How many requests."""
        return self.counts["requests"]

    @property
    def tokens(self) -> int:
        """Input plus output tokens: what a token limit counts."""
        return self.counts["input_tokens"] + self.counts["output_tokens"]

    def add(self, row: Mapping[Any, Any]) -> None:
        """Add one summed rollup row (the ``COUNTS``, ``cost`` and ``currency``)."""
        for name in COUNTS:
            self.counts[name] += int(row.get(name) or 0)
        cost, currency = row.get("cost"), row.get("currency")
        if cost is not None and currency:
            self.cost[str(currency)] = self.cost.get(str(currency), Decimal(0)) + Decimal(str(cost))

    def merge(self, other: Measures) -> None:
        """Add *other*'s figures to these."""
        for name in COUNTS:
            self.counts[name] += other.counts[name]
        for currency, amount in other.cost.items():
            self.cost[currency] = self.cost.get(currency, Decimal(0)) + amount

    def as_json(self) -> dict[str, Any]:
        """The figures as the API shows them (``Measures`` in the API contract)."""
        cost = {currency: float(amount) for currency, amount in sorted(self.cost.items())}
        return {**self.counts, "cost": cost}


def hidden() -> dict[str, Any]:
    """The figures of a cell that is not shown (fewer than k people): every measure ``None``."""
    return {**dict.fromkeys(COUNTS), "cost": None}
