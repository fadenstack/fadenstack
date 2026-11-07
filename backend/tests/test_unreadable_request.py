"""A request FastAPI cannot read is refused without repeating what was sent."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

#: Past the new password's 1024 characters.
SENT = "correct-horse-battery-staple-" * 40


@pytest.mark.anyio
async def test_a_refused_password_is_not_repeated(client: AsyncClient) -> None:
    """The reset page's own route: FastAPI's 422 repeated the password it refused."""
    answer = await client.post("/api/auth/reset-link/use", json={"token": "t" * 32, "password": SENT})
    assert answer.status_code == 422
    assert "correct-horse" not in answer.text
    assert answer.json() == {
        "detail": [
            {"type": "string_too_long", "loc": ["body", "password"], "msg": "String should have at most 1024 characters"},
        ],
    }
