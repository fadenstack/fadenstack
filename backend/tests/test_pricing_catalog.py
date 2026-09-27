"""Price catalog CRUD (services/observability/pricing_service.py): one active price per model."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from faden_backend.services.observability.pricing_service import PricingCRUDService


class _Session:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> Any:
        self.executed.append((str(stmt), dict(params or {})))
        result = MagicMock()
        result.mappings.return_value.one.return_value = {"id": "new", **(params or {})}
        return result

    async def commit(self) -> None:
        pass


@pytest.mark.anyio
async def test_a_new_price_replaces_the_active_one_instead_of_failing() -> None:
    """The unique index allows one active price per provider and model; adding one made a 500."""
    session = _Session()
    await PricingCRUDService(session).create(  # type: ignore[arg-type]
        provider=" OpenRouter ", model="DeepSeek/DeepSeek-V4.1-Flash",
        input_price_per_1k=Decimal("0.0001"), output_price_per_1k=Decimal("0.0004"),
    )
    (retire_sql, retire), (insert_sql, insert) = session.executed
    assert "UPDATE price_catalog SET active = FALSE" in retire_sql
    assert retire["provider"] == "openrouter" and retire["model"] == "deepseek/deepseek-v4.1-flash"
    assert "INSERT INTO price_catalog" in insert_sql
    assert (insert["provider"], insert["model"]) == (retire["provider"], retire["model"])
