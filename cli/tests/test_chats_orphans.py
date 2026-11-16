"""``faden chats orphans --erase`` says when a file could not be deleted yet (the next purge retries it)."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from faden_cli.commands import chats


def _gateway(monkeypatch: pytest.MonkeyPatch, *, failed_files: int) -> None:
    def orphans(*, erase: bool) -> dict[str, Any]:
        found = {"ok": True, "accounts": {"gone": {"chats": 1, "files": 2}}}
        if erase:
            return {**found, "erased": {"gone": 1}, "failed_files": failed_files}
        return {**found, "erased": None, "failed_files": None}

    monkeypatch.setattr(chats, "_orphans", orphans)
    monkeypatch.setattr("faden_cli.core.backend_tool.record_event", lambda *a, **k: None)


def test_a_file_that_could_not_be_deleted_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    _gateway(monkeypatch, failed_files=1)
    result = CliRunner().invoke(chats.chats_group, ["orphans", "--erase", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Erased the chat data of 1 deleted account(s)." in result.output
    assert "1 file(s) could not be deleted yet" in result.output and "next" in result.output


def test_a_whole_erase_says_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    _gateway(monkeypatch, failed_files=0)
    result = CliRunner().invoke(chats.chats_group, ["orphans", "--erase", "--yes"])
    assert result.exit_code == 0, result.output
    assert "could not be deleted" not in result.output
