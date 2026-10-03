"""A level of reasoning the model refuses goes again at the nearest one it takes, or without one.

``reasoning_effort`` is the client's preference: the Office agents send
"none" with every request, and a model with levels of its own refuses one it
lacks. The client never sees that refusal; it gets the model's answer at the
nearest level, or at the model's default.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import litellm
import pytest

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import llm_adapter
from faden_gateway.services.gateway.llm_adapter import (
    CompletionResult,
    LLMAdapter,
    _effort_instead,
    _with_effort,
)
from tests.test_llm_adapter_direct import _stream

#: OpenAI's refusal of a level a reasoning model lacks.
LISTED = (
    "litellm.BadRequestError: OpenAIException - Unsupported value: 'reasoning_effort' does not support 'none' "
    "with this model. Supported values are: 'low', 'medium', and 'high'."
)
#: A model without levels.
NO_LEVELS = "litellm.BadRequestError: OpenAIException - Unrecognized request argument supplied: reasoning_effort"


@pytest.fixture(autouse=True)
def fresh_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model is remembered as refusing, before or after the test."""
    monkeypatch.setattr(llm_adapter, "_EFFORT_INSTEAD", {})
    monkeypatch.setattr(llm_adapter, "_SYSTEM_FIRST", set())


# ── Where a refused level goes ──────────────────────────────────────


@pytest.mark.parametrize(
    ("refused", "detail", "instead"),
    [
        ("none", LISTED, "low"),
        ("xhigh", LISTED.replace("'none'", "'xhigh'"), "high"),
        ("minimal", "Supported values are: 'none', 'low', 'medium', and 'high'.", "low"),
        ("none", json.dumps({"error": {"message": 'Supported values are: "medium" and "high"'}}), "medium"),
        ("none", "Budget 0 is invalid. This model only works in thinking mode.", "low"),
        ("xhigh", "effort='xhigh' is not supported by this model. Got model: claude", "high"),
        ("max", "effort='max' is not supported by this model.", "high"),
        ("high", NO_LEVELS, None),
        ("unheard-of", "Invalid reasoning_effort: 'unheard-of'. Must be one of: 'low', 'medium', 'high'", None),
    ],
    ids=["listed", "listed-down", "tie-toward-medium", "listed-in-json", "always-thinks", "xhigh", "max",
         "no-levels", "unknown"],
)
def test_a_refused_level_goes_to_the_nearest_the_model_takes(refused: str, detail: str, instead: str | None) -> None:
    assert _effort_instead("m", refused, detail) == instead


def test_a_level_the_model_refused_before_is_not_asked_for_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_adapter, "_EFFORT_INSTEAD", {("m", "none"): "low", ("m", "low"): ""})
    assert _effort_instead("m", "minimal", "Supported values are: 'none', 'low' and 'medium'") == "medium"
    assert _effort_instead("m", "minimal", "reasoning_effort 'minimal' is not supported") is None


def test_vllms_level_through_litellm_changes_in_extra_body() -> None:
    template = {"enable_thinking": True}
    request = {"model": "m", "extra_body": {"reasoning_effort": "high", "chat_template_kwargs": template}}
    assert _with_effort(request, "low")["extra_body"] == {"chat_template_kwargs": template, "reasoning_effort": "low"}
    assert _with_effort(request, None)["extra_body"] == {"chat_template_kwargs": template}
    assert _with_effort({"model": "m", "reasoning_effort": "none"}, None) == {"model": "m"}


# ── Through LiteLLM ─────────────────────────────────────────────────


def _model(monkeypatch: pytest.MonkeyPatch, takes: set[str | None], refusal: str) -> list[str | None]:
    """A model that answers at the levels it *takes* (None: no level asked for) and refuses the rest."""
    sent: list[str | None] = []

    async def acompletion(**kwargs: Any) -> Any:
        sent.append(kwargs.get("reasoning_effort"))
        if kwargs.get("reasoning_effort") not in takes:
            raise litellm.exceptions.BadRequestError(message=refusal, model="m", llm_provider="openai")
        if kwargs["stream"]:
            return _no_chunks()
        return SimpleNamespace(model_dump=lambda: {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return sent


async def _no_chunks() -> AsyncIterator[Any]:
    for _ in ():
        yield _


async def _answer(effort: str) -> CompletionResult:
    result = await LLMAdapter().completion(
        provider_type=ProviderType.REMOTE_OPENAI, base_url="http://api.local/v1", api_key_encrypted=None,
        litellm_provider=None, litellm_model=None, extra_params=None, stream=False,
        payload={"model": "o3", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": effort},
    )
    assert isinstance(result, CompletionResult)
    return result


@pytest.mark.anyio
async def test_a_refused_level_goes_again_at_the_nearest_and_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _model(monkeypatch, takes={"low", "medium", "high"}, refusal=LISTED)
    assert (await _answer("none")).status_code == 200
    assert sent == ["none", "low"]

    # Remembered: the next request asks for "low" at once.
    assert (await _answer("none")).status_code == 200
    assert sent == ["none", "low", "low"]


@pytest.mark.anyio
async def test_a_model_without_levels_answers_at_its_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """One step, then no level at all; from then on no level at once."""
    sent = _model(monkeypatch, takes={None}, refusal=NO_LEVELS)
    assert (await _answer("none")).status_code == 200
    assert sent == ["none", "low", None]

    assert (await _answer("none")).status_code == 200
    assert sent[3:] == [None]


@pytest.mark.anyio
async def test_a_streamed_chat_goes_again_before_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _model(monkeypatch, takes={"high"}, refusal=LISTED.replace("'none'", "'xhigh'"))
    out = await _stream(LLMAdapter(), provider_type=ProviderType.REMOTE_OPENAI, payload={
        "model": "o3", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "xhigh",
    })
    assert sent == ["xhigh", "high"]
    assert out == [b"data: [DONE]\n\n"], "no error reached the client"


@pytest.mark.anyio
async def test_other_refusals_reach_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a refusal that names reasoning goes again, and only for a request that asked for a level."""
    sent = _model(monkeypatch, takes=set(), refusal="max_tokens is too large")
    result = await _answer("none")
    assert result.status_code == 400 and sent == ["none"]

    sent = _model(monkeypatch, takes={"low"}, refusal=NO_LEVELS)
    result = await LLMAdapter().completion(
        provider_type=ProviderType.REMOTE_OPENAI, base_url="http://api.local/v1", api_key_encrypted=None,
        litellm_provider=None, litellm_model=None, extra_params=None, stream=False,
        payload={"model": "o3", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert isinstance(result, CompletionResult)
    assert result.status_code == 400 and sent == [None]


# ── Straight to vLLM ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_direct_stream_goes_again_without_the_level() -> None:
    """A vLLM whose model has no levels: the client's "xhigh" (sent as "high") goes, and the answer comes."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"object": "error", "message": "reasoning_effort is not supported"})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await _stream(LLMAdapter(http_client=http), payload={
            "model": "qwen", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "xhigh",
        })
    assert [body.get("reasoning_effort") for body in seen] == ["high", None]
    assert out == [b"data: [DONE]\n\n"]
