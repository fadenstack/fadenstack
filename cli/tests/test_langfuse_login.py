"""Langfuse's first user: an address Langfuse takes, corrected on upgrade, and checked after deploy.

Langfuse 3.227.1 refused admin@localhost (what faden deploy -y makes) and admin@10.0.0.5, and took
admin@localhost.local and admin@example.com (tried against the image, 19 November 2026).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_cli.commands.deploy import langfuse_login_env
from faden_cli.core import langfuse_login, restore_swap
from faden_cli.core.env_gen import read_env_file, set_env_values

URL = "postgresql://langfuse:pw@postgres:5432/langfuse"


@pytest.mark.parametrize(
    ("email", "address"),
    [
        ("admin@localhost", "admin@localhost.local"),
        ("admin@10.0.0.5", "admin@10.0.0.5.local"),
        ("admin@example.com", "admin@example.com"),
        ("admin@localhost.local", "admin@localhost.local"),
        ("Ops.Team@Example.internal", "Ops.Team@Example.internal"),
        ("admin@ai", "admin@ai.local"),
        ("not an address", langfuse_login.FALLBACK),
    ],
)
def test_the_address_is_one_langfuse_takes(email: str, address: str) -> None:
    assert langfuse_login.address_for_langfuse(email) == address


def test_deploy_gives_langfuse_an_address_it_takes() -> None:
    assert langfuse_login_env({}, "admin@localhost")["LANGFUSE_INIT_USER_EMAIL"] == "admin@localhost.local"
    assert langfuse_login_env({}, "ops@example.com")["LANGFUSE_INIT_USER_EMAIL"] == "ops@example.com"


def test_upgrade_corrects_an_address_langfuse_refused(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    set_env_values(env, {"LANGFUSE_INIT_USER_EMAIL": "admin@localhost", "KEEP": "as it was"})
    assert langfuse_login.correct_env(env) == "admin@localhost.local"
    assert read_env_file(env)["LANGFUSE_INIT_USER_EMAIL"] == "admin@localhost.local"
    assert read_env_file(env)["KEEP"] == "as it was"
    assert langfuse_login.correct_env(env) is None  # once


@pytest.mark.parametrize("values", [{"LANGFUSE_INIT_USER_EMAIL": "ops@example.com"}, {}, None])
def test_upgrade_leaves_an_address_langfuse_takes(tmp_path: Path, values: dict[str, str] | None) -> None:
    env = tmp_path / ".env"
    if values is not None:
        set_env_values(env, {"OTHER": "x", **values})
    before = env.read_bytes() if env.exists() else None
    assert langfuse_login.correct_env(env) is None
    assert (env.read_bytes() if env.exists() else None) == before


def test_the_first_user_is_looked_up_in_langfuses_database(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, str]] = []

    def psql(sql: str, *, db: str, docker: str) -> str:
        asked.append((sql, db))
        return "1"

    monkeypatch.setattr(restore_swap, "psql", psql)
    env = {"LANGFUSE_INIT_USER_EMAIL": "o'brien@example.com", "LANGFUSE_DATABASE_URL": URL}
    assert langfuse_login.has_first_user(env, wait_s=0)
    sql, db = asked[0]
    assert db == "langfuse"
    assert "lower('o''brien@example.com')" in sql  # quoted, not pasted


def test_no_first_user_is_said_after_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore_swap, "psql", lambda sql, *, db, docker: "0")
    env = {"LANGFUSE_INIT_USER_EMAIL": "admin@localhost.local", "LANGFUSE_DATABASE_URL": URL}
    assert not langfuse_login.has_first_user(env, wait_s=0)
    assert not langfuse_login.has_first_user({"LANGFUSE_DATABASE_URL": URL}, wait_s=0)


def test_the_check_waits_while_langfuse_makes_its_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([restore_swap.PostgresError('relation "users" does not exist'), "0", "1"])

    def psql(sql: str, *, db: str, docker: str) -> str:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(restore_swap, "psql", psql)
    env = {"LANGFUSE_INIT_USER_EMAIL": "admin@localhost.local", "LANGFUSE_DATABASE_URL": URL}
    assert langfuse_login.has_first_user(env, wait_s=5, every_s=0)
