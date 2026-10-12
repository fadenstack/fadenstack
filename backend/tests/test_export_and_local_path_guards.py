"""Two small guards: spreadsheet formulas in the CSV export, and the model-store check."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from faden_backend.services.llm.service import LLMService
from faden_backend.services.observability.service import ObservabilityService, _csv_cell
from faden_backend.settings import settings


@pytest.mark.parametrize("cell", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx", "\rx"])
def test_text_that_starts_like_a_formula_is_quoted(cell: str) -> None:
    assert _csv_cell(cell) == "'" + cell


@pytest.mark.parametrize(("value", "expected"), [("gpt-4", "gpt-4"), (-3, "-3"), (0.5, "0.5"), (None, "")])
def test_other_values_are_unchanged(value: object, expected: str) -> None:
    assert _csv_cell(value) == expected


@pytest.mark.anyio()
async def test_the_export_quotes_a_formula_in_a_request_log_cell() -> None:
    row = {"model_alias": '=HYPERLINK("http://x")', "latency_ms": -1}
    result = MagicMock()
    result.keys.return_value = list(row)
    result.mappings.return_value = [row]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    now = datetime.now(timezone.utc)
    chunks = [c async for c in ObservabilityService(session).export_csv(now, now)]
    assert chunks[1].startswith("\"'=HYPERLINK(")
    assert chunks[1].rstrip().endswith(",-1")


@pytest.mark.anyio()
async def test_a_sibling_folder_with_the_store_name_as_prefix_is_outside_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "models"
    inside = store / "m1"
    evil = tmp_path / "models-evil"
    inside.mkdir(parents=True)
    evil.mkdir()
    monkeypatch.setattr(settings, "model_store_root", str(store))
    dao = MagicMock()
    dao.create = AsyncMock(return_value=SimpleNamespace(id="m"))
    artifacts = MagicMock()
    artifacts.create_batch = AsyncMock()
    service = LLMService.__new__(LLMService)

    await service.register_local_model(dao, artifacts, display_name="ok", path=str(inside))
    dao.create.assert_awaited_once()

    dao.create.reset_mock()
    for outside in (evil, store / ".." / "models-evil"):
        with pytest.raises(ValueError, match="outside the model store"):
            await service.register_local_model(dao, artifacts, display_name="no", path=str(outside))
    dao.create.assert_not_called()
