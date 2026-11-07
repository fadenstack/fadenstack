"""A request FastAPI cannot read is refused without repeating what was sent."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from faden_gateway.web.application import get_app

SENT = "sk-live-0123456789abcdef"


class _Probe(BaseModel):
    token: str = Field(max_length=8)


@pytest.mark.anyio
async def test_a_422_does_not_repeat_the_values_sent() -> None:
    app = get_app()

    @app.post("/probe-unreadable")
    async def probe(body: _Probe) -> dict[str, str]:
        del body
        return {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.post("/probe-unreadable", json={"token": SENT, "other": SENT})
    assert answer.status_code == 422
    assert SENT not in answer.text
    assert answer.json() == {
        "detail": [
            {"type": "string_too_long", "loc": ["body", "token"], "msg": "String should have at most 8 characters"},
        ],
    }
