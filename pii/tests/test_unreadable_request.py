"""A request FastAPI cannot read is refused without repeating what was sent."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from faden_pii.web.application import get_app

SENT = "Anna Schmidt, anna.schmidt@example.com"


class _Probe(BaseModel):
    text: str = Field(max_length=8)


@pytest.mark.anyio
async def test_a_422_does_not_repeat_the_values_sent() -> None:
    """FastAPI's own 422 repeated each value it refused (``input``)."""
    app = get_app()

    @app.post("/probe-unreadable")
    async def probe(body: _Probe) -> dict[str, str]:
        del body
        return {}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sent = {"text": SENT, "other": SENT}
        answer = await client.post("/probe-unreadable", json=sent)
    assert answer.status_code == 422
    assert SENT not in answer.text
    assert answer.json() == {
        "detail": [
            {
                "type": "string_too_long",
                "loc": ["body", "text"],
                "msg": "String should have at most 8 characters",
            },
        ],
    }
