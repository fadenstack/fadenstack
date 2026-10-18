"""Two small guards: spreadsheet formulas in the CSV export, and the model-store check."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm import service as llm_service_module
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.llm.service import LLMService
from faden_backend.services.observability.service import ObservabilityService, _csv_cell
from faden_backend.settings import settings
from faden_backend.web.api.llm.dependencies import get_llm_service


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
    for outside in (evil, store / ".." / "models-evil", store):  # a model is a folder in the store, not the store
        with pytest.raises(ValueError, match="outside the model store"):
            await service.register_local_model(dao, artifacts, display_name="no", path=str(outside))
    dao.create.assert_not_called()


@pytest.mark.anyio()
async def test_the_scan_reads_the_folder_the_check_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the path as given again: a link changed in between led the scan elsewhere."""
    store = tmp_path / "models"
    (store / "m1").mkdir(parents=True)
    monkeypatch.setattr(settings, "model_store_root", str(store))
    scanned: list[str] = []
    monkeypatch.setattr(llm_service_module, "scan_model_directory", lambda path: scanned.append(path) or [])
    dao = MagicMock()
    dao.create = AsyncMock(return_value=SimpleNamespace(id="m"))
    service = LLMService.__new__(LLMService)

    await service.register_local_model(dao, MagicMock(), display_name="ok", path=str(store / "m1" / ".." / "m1"))
    assert scanned == [os.path.realpath(store / "m1")]


@pytest.mark.anyio()
async def test_registering_a_path_outside_the_store_is_a_422(
    client: AsyncClient, fastapi_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It was a 500."""
    monkeypatch.setattr(settings, "model_store_root", str(tmp_path / "models"))
    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser = uuid.uuid4(), True, True
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    service = LLMService(SimpleNamespace(), gateway_sync=GatewaySyncService(None))  # type: ignore[arg-type]
    fastapi_app.dependency_overrides[get_llm_service] = lambda: service

    resp = await client.post("/api/llm/models/register", json={"display_name": "x", "path": "/etc"})
    assert resp.status_code == 422, resp.text
    assert "outside the model store" in resp.json()["detail"]
