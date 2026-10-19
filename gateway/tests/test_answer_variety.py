"""Watermarked answers to the same request differ; n answers are n answers; long whole answers have time.

vLLM's watermark gives the same request the same answer, ``n`` copies and ``seed`` nothing to change
(measured on the release server). The gateway ends what goes to a watermarking model in an invisible
variation, asks for ``n`` answers one by one, each with its own receipt, and refuses ``n`` on a stream.
Any other route gets the request as it was sent. Beside it: what the model hears of a tool that returned
nothing.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import answer_variety, picture_marking
from faden_gateway.services.gateway import service as service_module
from tests.test_text_receipts import (  # noqa: F401 - the fixtures
    LONG,
    _answers,
    _ask,
    _Engine,
    _seeded,
    _work_ends_before_the_database,
    engine,
)

QUESTION = "What is on page 12?"
WATERMARK = {"watermark": {"algorithm": "gumbel", "key_version": 2, "context_width": 4}}
INVISIBLE = re.compile("^[\u2060\u200b]{6}$")


def _route(provider: ProviderType = ProviderType.VLLM, *, marks: bool = True, extra: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        provider_type=provider, node_metadata=WATERMARK if marks else None, extra_params=extra, litellm_provider=None,
    )


def _asked(content: Any = QUESTION, **extra: Any) -> dict[str, Any]:
    return {"model": "qwen", "messages": [{"role": "system", "content": "Be brief."},
                                          {"role": "user", "content": content}], **extra}


def _ending(payload: dict[str, Any], sent: str = QUESTION) -> str:
    """What the request's last message got at its end."""
    content = payload["messages"][-1]["content"]
    assert content.startswith(sent)
    return content[len(sent):]


# ── The variation ─────────────────────────────────────────────────


def test_a_watermarking_route_gets_the_last_message_ended_invisibly() -> None:
    asked = _asked()
    varied = answer_variety.vary(asked, _route())
    assert INVISIBLE.match(_ending(varied))
    assert varied["messages"][0] == asked["messages"][0], "only the last message changes: the cache holds"
    assert asked["messages"][-1]["content"] == QUESTION, "the request as sent is left as it was"


@pytest.mark.parametrize(("route", "asked"), [
    pytest.param(_route(marks=False), _asked(), id="runtime-without-watermark"),
    pytest.param(_route(ProviderType.REMOTE_OPENAI), _asked(), id="remote"),
    pytest.param(_route(), _asked(temperature=0), id="greedy"),
    pytest.param(_route(extra={"watermarking": False}), _asked(), id="switched-off"),
])
def test_any_other_route_gets_the_request_as_sent(route: Any, asked: dict[str, Any]) -> None:
    assert answer_variety.vary(asked, route) is asked


def test_the_variation_ends_the_last_text_and_nothing_the_model_is_to_continue() -> None:
    parts = [{"type": "text", "text": QUESTION}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
    varied = answer_variety.vary(_asked(parts), _route())["messages"][-1]["content"]
    assert varied[1] == parts[1] and INVISIBLE.match(varied[0]["text"][len(QUESTION):])
    only_picture = answer_variety.vary(_asked([parts[1]]), _route())["messages"][-1]["content"]
    assert only_picture[0] == parts[1] and INVISIBLE.match(only_picture[1]["text"])
    tool = {"model": "qwen", "messages": [{"role": "tool", "tool_call_id": "c1", "content": "Found 3 pages."}]}
    assert INVISIBLE.match(answer_variety.vary(tool, _route())["messages"][-1]["content"][len("Found 3 pages."):])
    prefill = {"model": "qwen", "messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Dear"}]}
    assert answer_variety.vary(prefill, _route()) is prefill


def test_the_same_seed_gives_the_same_variation_and_none_a_new_one() -> None:
    seeded = [_ending(answer_variety.vary(_asked(seed=7), _route())) for _ in range(3)]
    assert len(set(seeded)) == 1
    assert _ending(answer_variety.vary(_asked(seed=8), _route())) != seeded[0] or (
        _ending(answer_variety.vary(_asked(seed=9), _route())) != seeded[0]
    )
    unseeded = {_ending(answer_variety.vary(_asked(), _route())) for _ in range(20)}
    assert len(unseeded) > 1


def test_the_variation_is_not_the_unicode_markers() -> None:
    """U+FEFF and the variation selectors carry the SDKs' invisible text mark: never these."""
    ends = "".join(_ending(answer_variety.vary(_asked(), _route())) for _ in range(30))
    assert "\ufeff" not in ends and not re.search("[\ufe00-\ufe0f]", ends)


# ── n answers ─────────────────────────────────────────────────────


def test_n_is_split_only_for_a_whole_answer_of_a_watermarking_route() -> None:
    assert answer_variety.split(_asked(n=3), _route(), stream=False) == 3
    assert answer_variety.split(_asked(), _route(), stream=False) is None
    assert answer_variety.split(_asked(n=1), _route(), stream=False) is None
    assert answer_variety.split(_asked(n=3), _route(marks=False), stream=False) is None
    assert answer_variety.split(_asked(n=3, temperature=0), _route(), stream=False) is None
    with pytest.raises(answer_variety.StreamedChoicesError):
        answer_variety.split(_asked(n=2), _route(), stream=True)
    with pytest.raises(ValueError, match="at most 16"):
        answer_variety.split(_asked(n=17), _route(), stream=False)


def test_the_answers_of_a_split_request_are_joined_in_order() -> None:
    answers = [
        {"id": "a", "choices": [{"index": 0, "message": {"content": f"Answer {i}"}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5 + i, "total_tokens": 15 + i}}
        for i in range(3)
    ]
    merged = answer_variety.merged(answers)
    assert merged["id"] == "a"
    assert [(c["index"], c["message"]["content"]) for c in merged["choices"]] == [
        (0, "Answer 0"), (1, "Answer 1"), (2, "Answer 2"),
    ]
    assert merged["usage"] == {"prompt_tokens": 30, "completion_tokens": 18, "total_tokens": 48}


# ── Through the gateway ───────────────────────────────────────────


@pytest.mark.anyio
async def test_a_watermarking_model_gets_the_variation_and_the_client_its_answer(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seeded(fastapi_app, db_session, node_metadata=WATERMARK)
    sent = _answers(monkeypatch)
    r = await _ask(client)
    assert r.status_code == 200, r.text
    [payload] = sent
    assert INVISIBLE.match(_ending(payload))


@pytest.mark.anyio
async def test_a_route_without_the_watermark_gets_the_message_as_sent(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seeded(fastapi_app, db_session)
    sent = _answers(monkeypatch)
    r = await _ask(client, n=3)
    assert r.status_code == 200, r.text
    [payload] = sent
    assert payload["messages"][-1]["content"] == QUESTION and payload["n"] == 3, "n goes to the model as asked"


@pytest.mark.anyio
async def test_n_answers_are_asked_for_one_by_one_each_with_its_receipt(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    engine: _Engine,  # noqa: F811 - the fixture
) -> None:
    await _seeded(fastapi_app, db_session, node_metadata=WATERMARK)
    sent = _answers(monkeypatch, LONG, completion_tokens=250)
    r = await _ask(client, n=3, events=True)
    assert r.status_code == 200, r.text
    assert len(sent) == 3 and all(p["n"] == 1 for p in sent)
    endings = [_ending(p) for p in sent]
    assert all(INVISIBLE.match(e) for e in endings)
    choices = r.json()["choices"]
    assert [c["index"] for c in choices] == [0, 1, 2]
    assert r.json()["usage"]["completion_tokens"] == 750
    assert len(engine.asked) == 3, "a receipt per answer"
    assert all(asked["watermark"]["applied"] == "model" for asked in engine.asked)
    header = r.headers[picture_marking.HEADER]
    assert all(receipt in header for receipt in engine.receipts)
    events = [e for e in r.json()["faden"]["events"] if e.get("name") == "faden.provenance"]
    assert [e["value"]["receipt"]["id"] for e in events] == engine.receipts


@pytest.mark.anyio
async def test_n_on_a_stream_of_a_watermarking_model_is_refused_not_copied(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seeded(fastapi_app, db_session, node_metadata=WATERMARK)
    sent = _answers(monkeypatch)
    r = await _ask(client, stream=True, n=2)
    assert r.status_code == 400, r.text
    assert "n_not_supported" in r.text and "stream" in r.text
    assert sent == [], "the model is not asked"


# ── A tool that returned nothing ──────────────────────────────────


@pytest.mark.parametrize("result", [None, "", "   \n"])
def test_the_model_hears_that_a_tool_returned_nothing(result: Any) -> None:
    assert service_module._or_no_result(result) == "The tool returned no result."  # noqa: SLF001


@pytest.mark.parametrize("result", ["[]", "{}", "0", "Found 3 pages."])
def test_a_tool_result_with_anything_in_it_is_left_as_it_is(result: str) -> None:
    assert service_module._or_no_result(result) == result  # noqa: SLF001
