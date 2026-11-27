"""The Apple collector reports what memory is available, not the total as free."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from faden_agent.gpu import apple


@pytest.mark.asyncio
async def test_apple_collector_reports_available_memory_not_total(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1 << 30
    out = '{"SPDisplaysDataType": [{"sppci_model": "Apple M4 Max"}]}'
    monkeypatch.setattr(apple, "_run", AsyncMock(return_value=(0, out, "")))
    monkeypatch.setattr(apple.psutil, "virtual_memory", lambda: SimpleNamespace(total=64 * gib, available=20 * gib))
    snap = await apple.AppleMetalCollector().snapshot()
    assert snap.total_vram_bytes == 64 * gib
    assert snap.free_vram_bytes == 20 * gib and snap.used_vram_bytes == 44 * gib
    assert snap.devices[0].memory_used_mib == 44 * 1024
