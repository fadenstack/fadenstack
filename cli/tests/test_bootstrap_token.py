"""The first administrator is made only for whoever holds the install's bootstrap token.

``POST /api/bootstrap`` asked for nothing, and ``faden deploy`` opens ports 80 and 443 before it bootstraps:
the first administrator went to whoever asked first. Deploy now writes a random token into ``.env``, the
compose file hands it to the backend, and the CLI sends it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from faden_cli.core import bootstrap
from faden_cli.core.env_gen import (
    BOOTSTRAP_TOKEN_KEY,
    SECRET_KEYS,
    default_env_vars,
    ensure_bootstrap_token,
    read_env_file,
)

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"


class _Answer:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What the CLI sends to the backend; it answers 201 with credentials."""
    sent: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> _Answer:
        sent.append({"url": url, **kwargs})
        return _Answer(201, {"email": kwargs["json"]["email"], "password": "generated", "api_token": "t"})

    monkeypatch.setattr(bootstrap.httpx, "post", post)
    return sent


def test_every_install_gets_its_own_token_and_keeps_it() -> None:
    first, second = default_env_vars()[BOOTSTRAP_TOKEN_KEY], default_env_vars()[BOOTSTRAP_TOKEN_KEY]
    assert len(first) >= 32  # noqa: PLR2004
    assert first != second
    assert BOOTSTRAP_TOKEN_KEY in SECRET_KEYS, "regenerating .env keeps it"


def test_the_token_reaches_the_backend() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    backend = compose.split("\n  faden-backend:\n", 1)[1].split("\n  faden-", 1)[0]
    assert "FADEN_BACKEND_BOOTSTRAP_TOKEN: ${FADEN_BACKEND_BOOTSTRAP_TOKEN:-}" in backend


def test_an_env_from_before_the_token_gets_one_and_keeps_the_rest(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=pgpw\nHF_CACHE_DIR=/models/hf\n", encoding="utf-8")

    assert ensure_bootstrap_token(env) is True
    values = read_env_file(env)
    assert values["POSTGRES_PASSWORD"] == "pgpw"
    assert values["HF_CACHE_DIR"] == "/models/hf"
    token = values[BOOTSTRAP_TOKEN_KEY]
    assert token

    assert ensure_bootstrap_token(env) is False, "a token already there stays"
    assert read_env_file(env)[BOOTSTRAP_TOKEN_KEY] == token


def test_the_cli_sends_the_installs_token(
    tmp_path: Path, posted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(f"{BOOTSTRAP_TOKEN_KEY}=the-token\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "check_needs_bootstrap", lambda _url: True)

    creds = bootstrap.bootstrap_interactive("https://console.example", tmp_path, auto_confirm=True)

    assert creds is not None
    assert creds["email"] == "admin@localhost"
    assert posted[0]["url"] == "https://console.example/api/bootstrap"
    assert posted[0]["headers"] == {"X-Faden-Bootstrap-Token": "the-token"}


def test_without_a_token_nothing_is_sent_in_its_place(posted: list[dict[str, Any]]) -> None:
    bootstrap.run_bootstrap("https://console.example", token=None)
    assert posted[0]["headers"] is None


def test_a_refusal_says_where_the_token_comes_from(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    detail = "Bootstrap is refused: this server has no bootstrap token."
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *_a, **_k: _Answer(403, {"detail": detail}))

    assert bootstrap.run_bootstrap("https://console.example", token="stale") is None  # noqa: S106
    out = " ".join(capsys.readouterr().out.split())
    assert detail in out
    assert BOOTSTRAP_TOKEN_KEY in out
    assert "faden up" in out


def test_an_unreachable_backend_is_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_a: Any, **_k: Any) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(bootstrap.httpx, "post", down)
    assert bootstrap.run_bootstrap("https://console.example", token="t") is None  # noqa: S106
