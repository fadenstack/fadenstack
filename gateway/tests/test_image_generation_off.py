"""Image generation is off in 0.7.1 (EU AI Act Art. 5 from 2 December 2026): no picture is made, none leaves.

Both images routes refuse before anything runs (no model asked, nothing charged); a chat that asks for
pictures is refused before the model; a chat answer that carries a picture anyway is refused in its place,
whole or streamed, the provenance module on or off; an agent's profile offers no pictures. A fresh install
and one upgraded with an image model hosted refuse alike (V16).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, ProviderType
from faden_gateway.services.gateway import image_generation
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.services.gateway.provenance_client import ProvenanceClient
from tests.test_agent_routes import _headers, _profile, backend  # noqa: F401 - the fixture the agent routes need
from tests.test_gateway_images import IMAGES, PICTURE, _Recording
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _token
from tests.test_picture_marking import (
    _chat,
    _chat_answers,
    _chunk,
    _Engine,
    _paints,
    _streamed_chat,
    _streams,
)
from tests.test_provenance_module import BASE, TOKEN
from tests.test_text_receipts import ALLOWED_UNMARKED

PICTURE_PART = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PICTURE}"}}
INSTALLS = [
    pytest.param({"task": "chat"}, id="fresh"),  # no image model: 0.7.1 installed new
    pytest.param(IMAGES, id="upgraded"),  # the image model 0.7.0 hosted, still published
]


@pytest.fixture(params=["off", "on"])
async def marking(
    request: pytest.FixtureRequest, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_Engine | None]:
    """The provenance module off (``None``), or on with a fake engine that would mark the pictures."""
    if request.param == "off":
        yield None
        return
    fake = _Engine()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        client = ProvenanceClient(base_url=BASE, http_client=http, service_token=TOKEN)
        monkeypatch.setattr(fastapi_app.state, "provenance_client", client, raising=False)
        yield fake


def _refused_with_the_note(r: httpx.Response) -> None:
    assert r.status_code == 403, r.text
    error = r.json()["error"]
    assert (error["type"], error["code"]) == ("invalid_request_error", "images_disabled")
    assert error["message"] == image_generation.NOTE
    assert "data" not in r.json() and "choices" not in r.json()
    assert PICTURE not in r.text


def test_it_is_off_in_this_version() -> None:
    assert image_generation.ENABLED is False
    error = image_generation.refusal()
    assert (error.status_code, error.error_type, error.code) == (403, "invalid_request_error", "images_disabled")
    assert "EU AI Act (Article 5)" in error.message and "2 December 2026" in error.message


def test_no_setting_turns_it_on() -> None:
    """Neither the settings nor the environment know it: only the code does."""
    from faden_gateway import settings as settings_module

    source = open(settings_module.__file__, encoding="utf-8").read().lower()  # noqa: PTH123, SIM115
    assert "image_generation" not in source and "images_enabled" not in source


# ── The images routes ─────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("install", INSTALLS)
async def test_the_images_route_refuses_before_anything_runs(
    install: dict[str, Any], fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=install)
    fastapi_app.state.gateway_observability = _Recording()
    calls = _paints(monkeypatch)

    for body in ({"prompt": "A lighthouse"}, {"model": ALIAS, "prompt": "A lighthouse", "n": 2}, {"prompt": ""}):
        r = await client.post("/v1/images/generations", headers={"Authorization": f"Bearer {_token()}"}, json=body)
        _refused_with_the_note(r)

    assert calls == [], "no image model is asked"
    assert (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all() == [], "nothing is charged"


@pytest.mark.anyio
async def test_the_pipeline_refuses_an_images_request_from_any_caller() -> None:
    """Behind the routes, the shared pipeline refuses too: no other caller reaches an image model."""
    from faden_gateway.services.gateway.errors import GatewayError
    from faden_gateway.services.gateway.service import GatewayService

    with pytest.raises(GatewayError) as refused:
        await GatewayService.route_non_stream(
            object(),  # type: ignore[arg-type] - refused before the service is used
            auth=None,  # type: ignore[arg-type]
            endpoint="/v1/images/generations", payload={"model": ALIAS, "prompt": "A lighthouse"}, request_id="r-1",
        )
    assert (refused.value.status_code, refused.value.code) == (403, "images_disabled")


@pytest.mark.anyio
@pytest.mark.parametrize("install", INSTALLS)
async def test_an_agents_images_route_refuses_alike(
    install: dict[str, Any], client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: Any,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=install)
    calls = _paints(monkeypatch)

    r = await client.post(
        "/v1/agents/eplan-assistant/images/generations", headers=_headers(), json={"prompt": "A slide's picture"},
    )

    _refused_with_the_note(r)
    assert calls == []


@pytest.mark.anyio
async def test_the_profile_offers_no_pictures_with_an_image_model_hosted(
    client: AsyncClient, db_session: AsyncSession, backend: Any,  # noqa: F811
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=IMAGES)
    backend.put(_profile())

    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())

    assert r.status_code == 200, r.text
    assert r.json()["images"] is False, "the SDKs offer no picture tools"


# ── Chats ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("modalities", [["text", "image"], ["image"], "image", [" Image "]])
@pytest.mark.parametrize("stream", [False, True])
async def test_a_chat_that_asks_for_pictures_is_refused_before_the_model(
    modalities: Any, stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None, node_metadata=None)
    fastapi_app.state.gateway_observability = _Observability()
    asked: list[dict[str, Any]] = []

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        asked.append(kwargs)
        raise AssertionError("no model may be asked")

    monkeypatch.setattr(LLMAdapter, "completion", completion)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "modalities": modalities,
        "messages": [{"role": "user", "content": "Draw a lighthouse"}],
    })

    _refused_with_the_note(r)
    assert asked == []


@pytest.mark.anyio
async def test_a_chat_that_asks_for_text_is_answered(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None, node_metadata=None)
    fastapi_app.state.gateway_observability = _Observability()
    _chat_answers(monkeypatch, {"content": "A lighthouse stands on a rock."})

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "modalities": ["text"], "messages": [{"role": "user", "content": "Describe a lighthouse"}],
    })

    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "A lighthouse stands on a rock."


def _picture_message(where: str) -> dict[str, Any]:
    if where == "images":
        return {"content": "Here it is.", "images": [{**PICTURE_PART, "index": 0}]}
    return {"content": [{"type": "text", "text": "Here it is."}, PICTURE_PART]}


@pytest.mark.anyio
@pytest.mark.parametrize("where", ["images", "content"])
async def test_a_chats_picture_is_refused_in_its_place(
    where: str, marking: _Engine | None, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that answers with a picture: the answer is refused, the picture never marked or sent."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None, node_metadata=ALLOWED_UNMARKED)
    fastapi_app.state.gateway_observability = _Observability()
    _chat_answers(monkeypatch, _picture_message(where))

    r = await _chat(client)

    _refused_with_the_note(r)
    assert "Here it is." not in r.text
    if marking is not None:
        assert marking.sent == [], "never marked: it never leaves"


@pytest.mark.anyio
@pytest.mark.parametrize("where", ["images", "content"])
async def test_a_streamed_chat_ends_with_the_refusal_where_its_picture_would_come(
    where: str, marking: _Engine | None, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=ALLOWED_UNMARKED)
    fastapi_app.state.gateway_observability = _Observability()
    _streams(
        monkeypatch,
        _chunk({"role": "assistant", "content": "Here it is."}),
        _chunk({"images": [{**PICTURE_PART, "index": 0}]} if where == "images" else {"content": [PICTURE_PART]}),
        _chunk({"content": " And a second sentence."}),
        _chunk({}, finish="stop"),
    )

    text = await _streamed_chat(client)

    assert "Here it is." in text
    assert PICTURE not in text
    assert "images_disabled" in text and image_generation.NOTE in text
    assert "And a second sentence." not in text, "nothing after the picture leaves"
    if marking is not None:
        assert marking.sent == []


# ── The pieces ────────────────────────────────────────────────────


async def _through(*chunks: bytes) -> list[bytes]:
    async def source() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    return [c async for c in image_generation.refuse_pictures(source())]


@pytest.mark.anyio
async def test_a_stream_without_pictures_passes_byte_for_byte() -> None:
    chunks = [
        _chunk({"content": "The field is called image_url "}),  # names a picture's field, carries none
        _chunk({"content": 'and "images" lists them.'}),
        b"data: [DONE]\n\n",
    ]
    assert await _through(*chunks) == chunks


@pytest.mark.anyio
async def test_a_picture_split_across_reads_is_still_refused() -> None:
    event = _chunk({"images": [PICTURE_PART]})
    half = len(event) // 2
    out = b"".join(await _through(_chunk({"content": "Before."}), event[:half], event[half:]))
    assert b"Before." in out and PICTURE.encode() not in out and b"images_disabled" in out


@pytest.mark.anyio
async def test_a_last_event_without_its_blank_line_is_read_too() -> None:
    out = b"".join(await _through(_chunk({"images": [PICTURE_PART]}).rstrip(b"\n")))
    assert PICTURE.encode() not in out and b"images_disabled" in out


def test_what_asks_for_pictures() -> None:
    asks = image_generation.asks_for_pictures
    assert asks({"modalities": ["text", "image"]}) and asks({"modalities": "image"})
    assert not asks({"modalities": ["text"]}) and not asks({}) and not asks({"modalities": None})


def test_what_carries_pictures() -> None:
    carries = image_generation.answer_carries_pictures
    assert carries({"choices": [{"message": {"images": [PICTURE_PART]}}]})
    assert carries({"choices": [{"message": {"content": [PICTURE_PART]}}]})
    assert not carries({"choices": [{"message": {"content": "image_url", "images": []}}]})
    assert not carries({"choices": [{"message": {"content": [{"type": "text", "text": "x"}]}}]})
    assert not carries({"data": [{"b64_json": PICTURE}]}) and not carries(None)
