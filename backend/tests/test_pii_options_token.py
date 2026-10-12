"""The backend presents the PII service token when it asks for the policy options."""

from __future__ import annotations

import httpx
import pytest

from faden_backend.settings import settings
from faden_backend.web.api.admin.pii import views


class _Recorder:
    def __init__(self) -> None:
        self.headers: dict[str, str] | None = None

    async def get(self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 0) -> httpx.Response:
        self.headers = headers
        return httpx.Response(200, json={"supported_entities": ["PERSON"]}, request=httpx.Request("GET", url))


@pytest.mark.anyio
@pytest.mark.parametrize(("token", "expected"), [("pii-secret", {"Authorization": "Bearer pii-secret"}), ("", {})])
async def test_options_are_asked_for_with_the_service_token(
    monkeypatch: pytest.MonkeyPatch, token: str, expected: dict[str, str],
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(views, "_get_pii_client", lambda: recorder)
    monkeypatch.setattr(settings, "pii_service_token", token)

    result = await views.get_pii_config_options()

    assert result["source"] == "pii_service"
    assert recorder.headers == expected
