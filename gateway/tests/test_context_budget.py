"""The web chat's context fits the model's window (0.7.2 K, layers 1 to 3).

Every turn had a fixed 4,096 tokens for the project's instructions, its files and the newest 20 messages; older
turns went without a word and most project files never fitted. Now the budget comes from the model's context
length, P = (W - R) x 0.9; the project's files go inline while all of them fit a quarter of it, else one index line
and attachment_search; the history is cut in batches by the replay rule, with a line in place of what was left out.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from faden_gateway.db.models.gateway import ExtractionStatus
from faden_gateway.services.gateway import context_assembler as ca
from faden_gateway.services.gateway.context_assembler import (
    DROPPED_PREFIX,
    DROPPED_SEARCHABLE,
    ContextAssembler,
    batch_cut,
    prompt_budget,
)
from faden_gateway.services.gateway.service import _reply_reserve

T0 = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)


def _message(i: int, chars: int = 400) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), role="user" if i % 2 == 0 else "assistant", content=f"m{i:03d} " + "x" * (chars - 5),
        token_estimate=None, status="completed", tool_call_json=None, created_at=T0 + timedelta(minutes=i),
    )


def _file(name: str, text: str | None, status: ExtractionStatus = ExtractionStatus.COMPLETED,
          project: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), filename=name, extracted_text=text, extraction_status=status, truncated=False,
        session_id=None if project else uuid.uuid4(), project_id=uuid.uuid4() if project else None,
        content_type="text/plain", storage_key=f"k/{name}",
    )


class _Dao:
    def __init__(self, messages: list[Any], *, project_files: list[Any] = (), own_files: list[Any] = ()) -> None:
        self.messages, self.project_files, self.own_files = messages, list(project_files), list(own_files)

    async def get_latest_summary(self, **_: Any) -> None:
        return None

    async def list_active_facts(self, **_: Any) -> list[Any]:
        return []

    async def list_attachments_for_session(self, **_: Any) -> list[Any]:
        return self.own_files

    async def list_attachments_for_project(self, **_: Any) -> list[Any]:
        return self.project_files

    async def get_recent_messages(self, *, limit: int, **_: Any) -> list[Any]:
        return self.messages[-limit:]

    async def count_messages(self, **_: Any) -> int:
        return len(self.messages)


async def _assemble(dao: _Dao, *, project: bool = False, **kwargs: Any) -> ca.AssembledContext:
    assembler = ContextAssembler(dao=dao, searchable=True, **kwargs)  # type: ignore[arg-type]
    return await assembler.assemble(
        session_id=uuid.uuid4(), tenant_id="t", user_id="u",
        current_messages=[{"role": "user", "content": "And now?"}],
        project=SimpleNamespace(id=uuid.uuid4(), system_instructions="Be brief.") if project else None,
    )


def _texts(result: ca.AssembledContext) -> list[str]:
    return [m["content"] for m in result.messages if isinstance(m.get("content"), str)]


# ── Layer 1: the budget ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("window", "reserve", "cap", "factor", "budget"), [
    (32_768, 4_096, None, 1.0, 25_804),   # (32,768 - 4,096) x 0.9
    (32_768, 8_192, None, 1.0, 22_118),   # thinking on: a larger reserve
    (32_768, 4_096, 8_000, 1.0, 8_000),   # the administrator's cap
    (32_768, 4_096, None, 0.75, 19_353),  # the one retry
    (None, 4_096, None, 1.0, 4_096),      # not known: as before
    (None, 4_096, 6_000, 1.0, 6_000),     # not known, a configured budget
    (3_000, 4_096, None, 1.0, 4_096),     # a window smaller than the reserve says nothing usable
])
def test_the_budget(window: int | None, reserve: int, cap: int | None, factor: float, budget: int) -> None:
    assert prompt_budget(window, reserve, cap=cap, factor=factor) == budget


@pytest.mark.parametrize(("payload", "reserve"), [
    ({}, 4_096),
    ({"max_tokens": 1_000}, 1_000),
    ({"max_completion_tokens": 2_000, "max_tokens": 1_000}, 2_000),
    ({"reasoning_effort": "high"}, 8_192),
    ({"reasoning_effort": "none"}, 4_096),
    ({"chat_template_kwargs": {"enable_thinking": True}}, 8_192),
    ({"chat_template_kwargs": {"enable_thinking": False}}, 4_096),
])
def test_the_replys_reserve(payload: dict[str, Any], reserve: int) -> None:
    assert _reply_reserve(payload) == reserve


@pytest.mark.anyio
async def test_the_window_sets_the_budget_and_an_unknown_one_keeps_todays() -> None:
    known = await _assemble(_Dao([]), window=32_768)
    unknown = await _assemble(_Dao([]))
    assert (known.budget, known.window) == (25_804, 32_768)
    assert (unknown.budget, unknown.window) == (4_096, None)


# ── Layer 3: the history, by the replay rule ──────────────────────────────────────────────────────────


def test_the_cut_moves_in_batches_down_to_sixty_percent() -> None:
    sizes = [100] * 11
    assert batch_cut(sizes[:10], 1_000) == 0      # 1,000: still fits
    assert batch_cut(sizes, 1_000) == 5           # 1,100 passes it: cut to 600
    # The next messages keep the cut where it was until the span passes the limit again.
    assert [batch_cut([100] * n, 1_000) for n in range(11, 16)] == [5, 5, 5, 5, 5]
    assert batch_cut([100] * 16, 1_000) == 10


def test_the_newest_message_is_always_kept() -> None:
    assert batch_cut([100, 100, 5_000], 1_000) == 2
    assert batch_cut([5_000], 1_000) == 0


def test_the_same_history_gives_the_same_cut() -> None:
    sizes = [37, 410, 88, 1_200, 15, 640, 70, 990, 300, 45, 820]
    assert len({batch_cut(sizes, 1_500) for _ in range(5)}) == 1


@pytest.mark.anyio
async def test_older_messages_are_left_out_with_a_line_and_the_question_always_goes() -> None:
    # About 100 tokens each; W 8,192 and R 4,096 leave a budget of 3,686.
    messages = [_message(i) for i in range(60)]
    result = await _assemble(_Dao(messages), window=8_192)

    texts = _texts(result)
    assert texts[-1] == "And now?"
    assert result.dropped > 0
    assert texts[0] == DROPPED_SEARCHABLE.format(n=result.dropped)
    kept = [t for t in texts if t.startswith("m")]
    assert kept[-1].startswith("m059"), "the newest message of the history is kept"
    assert kept[0].startswith(f"m{result.dropped:03d}"), "what is kept follows the cut without a gap"
    assert result.kept_from == messages[result.dropped].created_at
    assert result.total_token_estimate <= result.budget


@pytest.mark.anyio
async def test_a_short_chat_goes_whole_without_a_line() -> None:
    messages = [_message(i) for i in range(6)]
    result = await _assemble(_Dao(messages), window=32_768)
    assert result.dropped == 0 and result.kept_from == messages[0].created_at
    assert not any(t.startswith(DROPPED_PREFIX) for t in _texts(result))
    assert len([t for t in _texts(result) if t.startswith("m")]) == 6


@pytest.mark.anyio
async def test_twenty_messages_are_no_longer_the_limit() -> None:
    result = await _assemble(_Dao([_message(i, chars=40) for i in range(120)]), window=32_768,
                             max_recent_messages=200)
    assert result.dropped == 0
    assert len([t for t in _texts(result) if t.startswith("m")]) == 120


@pytest.mark.anyio
async def test_messages_past_the_read_cap_count_as_left_out() -> None:
    result = await _assemble(_Dao([_message(i, chars=40) for i in range(250)]), window=32_768,
                             max_recent_messages=200)
    assert result.dropped == 50
    assert _texts(result)[0] == DROPPED_SEARCHABLE.format(n=50)


@pytest.mark.anyio
async def test_the_history_is_at_most_its_cap_however_long_the_window() -> None:
    result = await _assemble(_Dao([_message(i, chars=4_000) for i in range(100)]), window=1_000_000,
                             history_cap=32_000)
    history = sum(len(t) // 4 for t in _texts(result) if t.startswith("m"))
    assert history <= 32_000 and result.dropped > 0


# ── Layer 2: the project's files ──────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_projects_files_go_inline_while_they_fit_a_quarter_of_the_budget() -> None:
    files = [_file("Contract.pdf", "c" * 4_000), _file("Price list.xlsx", "p" * 4_000)]
    result = await _assemble(_Dao([], project_files=files), project=True, window=32_768)
    assert any(t.startswith("[Attached: Contract.pdf]") for t in _texts(result))
    assert any(t.startswith("[Attached: Price list.xlsx]") for t in _texts(result))
    assert not result.project_files_indexed and result.searchable == []


@pytest.mark.anyio
async def test_past_a_quarter_the_whole_project_goes_as_one_index_line() -> None:
    # 2 x 4,000 tokens > 25% of 25,804; each alone would fit.
    files = [_file("Contract.pdf", "c" * 16_000), _file("Price list.xlsx", "p" * 16_000),
             _file("scan.pdf", None, ExtractionStatus.FAILED)]
    result = await _assemble(_Dao([], project_files=files), project=True, window=32_768)

    texts = _texts(result)
    assert result.project_files_indexed
    line = (
        "[Project files, not included in full: Contract.pdf, Price list.xlsx (2 files). "
        "Use attachment_search to look up what the question needs.]"
    )
    assert line in texts
    assert not any(t.startswith("[Attached: Contract.pdf]") for t in texts)
    assert [f.filename for f in result.searchable] == ["Contract.pdf", "Price list.xlsx"]
    assert "[Attached file: scan.pdf -- not sent: its text could not be read.]" in texts


@pytest.mark.anyio
async def test_the_chats_own_file_still_goes_inline_beside_an_indexed_project() -> None:
    files = [_file("Contract.pdf", "c" * 16_000), _file("Price list.xlsx", "p" * 16_000)]
    own = [_file("note.txt", "short note", project=False)]
    result = await _assemble(_Dao([], project_files=files, own_files=own), project=True, window=32_768)
    assert result.project_files_indexed
    assert any(t.startswith("[Attached: note.txt]") for t in _texts(result))
