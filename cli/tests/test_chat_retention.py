"""How long deleted chats are kept: asked once, kept in .env, stored unless an administrator chose."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.core import backend_tool, chat_retention
from faden_cli.core.env_gen import read_env_file, write_env_file


def test_unattended_it_is_30_days(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_file(env, {"POSTGRES_PASSWORD": "x"})
    assert chat_retention.ask_retention(env, days=None, yes=True, heading="") == 30
    assert read_env_file(env)[chat_retention.ENV_KEY] == "30"


def test_the_option_wins_and_a_stored_answer_is_not_asked_again(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_file(env, {})
    assert chat_retention.ask_retention(env, days=90, yes=False, heading="") == 90
    # Asked again (an upgrade): the answer in .env stands, nobody is asked.
    assert chat_retention.ask_retention(env, days=None, yes=False, heading="") == 90


def test_the_person_is_asked_when_nothing_is_set(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_file(env, {})

    import click  # noqa: PLC0415

    @click.command()
    def ask() -> None:
        click.echo(f"days={chat_retention.ask_retention(env, days=None, yes=False, heading='Deleted chats')}")

    result = CliRunner().invoke(ask, input="14\n")
    assert "days=14" in result.output
    assert "Days to keep deleted chats" in result.output


def test_an_administrators_choice_in_the_console_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def tool(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True, "value": 7} if args[0] == "get-setting" else {"ok": True}

    monkeypatch.setattr(backend_tool, "run_backend_tool", tool)
    chat_retention.apply_retention(30)
    assert [c[0] for c in calls] == ["get-setting"]  # never set over the administrator's 7


def test_without_a_stored_setting_the_answer_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def tool(args: list[str], **_: Any) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True, "value": None} if args[0] == "get-setting" else {"ok": True}

    monkeypatch.setattr(backend_tool, "run_backend_tool", tool)
    chat_retention.apply_retention(45)
    assert calls[1] == ["set-setting", "--key", "chat.deleted_retention_days", "--value", "45"]
