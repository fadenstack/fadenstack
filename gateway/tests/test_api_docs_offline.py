"""The API docs pages ask no third party for anything.

The console embeds them (/gateway-docs/). FastAPI's defaults loaded fonts from Google (ReDoc) and an icon from
fastapi.tiangolo.com (both); the scripts and styles were already served by the gateway itself.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc"])
async def test_the_docs_pages_load_only_from_their_own_origin(client: AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert re.findall(r"(?:src|href)=[\"'](https?://[^\"']+)", response.text) == []
