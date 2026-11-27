"""The infra agent's registration token is compared in constant time (0.7.2 H.4).

``header != f"Bearer {token}"`` stopped at the first differing byte, so the time an answer took told a caller how
much of a guessed token was right. Now ``hmac.compare_digest`` (OWASP ASVS 5.0, V11).
"""

from __future__ import annotations

import hmac
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from faden_backend.web.api.admin.system import views


def _request(header: str | None) -> SimpleNamespace:
    return SimpleNamespace(headers={} if header is None else {"Authorization": header})


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    value = "agent-token-0123456789abcdef"
    monkeypatch.setattr(views.settings, "system_agent_token", value)
    return value


def test_the_right_token_passes(token: str) -> None:
    views._require_agent_token(_request(f"Bearer {token}"))  # type: ignore[arg-type]


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer x", "bearer agent-token-0123456789abcdef",
                                    "Bearer agent-token-0123456789abcdeF", "Bearer agent-token-0123456789abcdef0",
                                    "Bearer агент"])
def test_any_other_header_is_refused(token: str, header: str | None) -> None:  # noqa: ARG001
    with pytest.raises(HTTPException) as refused:
        views._require_agent_token(_request(header))  # type: ignore[arg-type]
    assert refused.value.status_code == 401


def test_no_token_configured_lets_it_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views.settings, "system_agent_token", "")
    views._require_agent_token(_request(None))  # type: ignore[arg-type]


def test_the_comparison_is_hmacs(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []
    original = hmac.compare_digest

    def compare(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return original(a, b)

    monkeypatch.setattr(views.hmac, "compare_digest", compare)
    views._require_agent_token(_request(f"Bearer {token}"))  # type: ignore[arg-type]
    assert seen == [(f"Bearer {token}".encode(), f"Bearer {token}".encode())]
