"""/v1/images/generations: pictures from a prompt, by an image model the tenant runs (sdk/11).

The prompt goes through the pipeline like any text (limits, the PII check, the audit); the pictures come
back in the answer and stay out of traces and logs.
"""

from __future__ import annotations

from typing import Any

import pytest
import litellm
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from tests.test_gateway_pipeline_combined import ALIAS, _install_pii, _Observability, _pii, _seed, _token

IMAGES = {"task": "images"}
PICTURE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="


class _Recording(_Observability):
    """Keeps what a trace would store as the answer."""

    def __init__(self) -> None:
        super().__init__()
        self.outputs: list[Any] = []

    def record_success(self, *_: object, **kwargs: object) -> None:
        self.outputs.append(kwargs.get("output_payload"))


class _Painter:
    """The upstream image server: answers each prompt with a picture, keeps what it was sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        painter = self

        async def image_generation(self: LLMAdapter, **kwargs: Any) -> CompletionResult:
            painter.calls.append(kwargs)
            n = kwargs["payload"].get("n", 1)
            return CompletionResult(status_code=200, payload={"created": 1, "data": [{"b64_json": PICTURE}] * n})

        monkeypatch.setattr(LLMAdapter, "image_generation", image_generation)


async def _images(client: AsyncClient, **body: Any) -> Any:
    body.setdefault("prompt", "A servo motor on a packaging line, clean product photo")
    return await client.post("/v1/images/generations", headers={"Authorization": f"Bearer {_token()}"}, json=body)


@pytest.mark.anyio
async def test_pictures_come_back_in_the_answer_and_stay_out_of_the_trace(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=IMAGES)
    observability = _Recording()
    fastapi_app.state.gateway_observability = observability
    painter = _Painter(monkeypatch)

    r = await _images(client, model=ALIAS, n=2, size="1024x576", num_inference_steps=8, seed=7)

    assert r.status_code == 200, r.text
    assert [d["b64_json"] for d in r.json()["data"]] == [PICTURE, PICTURE]
    sent = painter.calls[0]["payload"]
    assert sent == {
        "model": ALIAS, "prompt": "A servo motor on a packaging line, clean product photo",
        "size": "1024x576", "n": 2, "num_inference_steps": 8, "seed": 7,
    }
    assert PICTURE not in str(observability.outputs), "the trace keeps sizes, not pictures"
    assert observability.outputs == [{"created": 1, "data": [{"b64_json": f"<{len(PICTURE)} characters>"}] * 2}]


@pytest.mark.anyio
async def test_without_a_model_the_tenants_image_model_makes_them(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=IMAGES)
    fastapi_app.state.gateway_observability = _Recording()
    painter = _Painter(monkeypatch)

    r = await _images(client)

    assert r.status_code == 200, r.text
    assert painter.calls[0]["payload"]["model"] == ALIAS


@pytest.mark.anyio
async def test_without_an_image_model_it_says_so(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata={"task": "chat"})
    fastapi_app.state.gateway_observability = _Recording()

    r = await _images(client)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_image_model"


@pytest.mark.anyio
async def test_the_prompt_is_pii_checked_like_any_text(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=_pii("tokenize_reversible"), node_metadata=IMAGES)
    fastapi_app.state.gateway_observability = _Recording()
    _install_pii(monkeypatch)
    painter = _Painter(monkeypatch)

    r = await _images(client, model=ALIAS, prompt="A portrait of Alice at the launch")

    assert r.status_code == 200, r.text
    prompt = painter.calls[0]["payload"]["prompt"]
    assert "Alice" not in prompt and "[PERSON_1]" in prompt


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "node_metadata", "body", "advice"),
    [
        ("/v1/images/generations", {"task": "chat"}, {"prompt": "x"}, "is a chat model"),
        ("/v1/images/generations", None, {"prompt": "x"}, "is a chat model"),  # a route that says nothing
        ("/v1/chat/completions", IMAGES, {"messages": [{"role": "user", "content": "hi"}]}, "send it to /v1/images/generations"),
    ],
)
async def test_a_model_of_another_kind_is_refused_in_plain_words(
    endpoint: str, node_metadata: dict[str, Any] | None, body: dict[str, Any], advice: str,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=node_metadata)
    fastapi_app.state.gateway_observability = _Recording()

    r = await client.post(endpoint, headers={"Authorization": f"Bearer {_token()}"}, json={"model": ALIAS, **body})

    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model_kind_mismatch"
    assert advice in r.json()["error"]["message"]


@pytest.mark.anyio
async def test_a_bad_request_is_refused_before_anything_runs(client: AsyncClient) -> None:
    for body in ({"prompt": ""}, {"prompt": "x", "n": 9}, {"prompt": "x", "size": "large"}, {"prompt": "x", "response_format": "url"}):
        r = await client.post("/v1/images/generations", headers={"Authorization": f"Bearer {_token()}"}, json=body)
        assert r.status_code == 400, body
        assert r.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "asks_for_b64"),
    [(ProviderType.VLLM, False), (ProviderType.REMOTE_OPENAI, True)],
)
async def test_what_goes_to_litellm(
    monkeypatch: pytest.MonkeyPatch, provider: ProviderType, asks_for_b64: bool,
) -> None:
    """A local image server is not asked for b64_json; the diffusion settings go in the body.

    LiteLLM refuses the setting for any model name OpenAI does not have, and the server answers that way anyway.
    """
    sent: dict[str, Any] = {}

    class _Answer:
        def model_dump(self) -> dict[str, Any]:
            return {"created": 1, "data": [{"b64_json": PICTURE}]}

    async def aimage_generation(**kwargs: Any) -> _Answer:
        sent.update(kwargs)
        return _Answer()

    monkeypatch.setattr(litellm, "aimage_generation", aimage_generation)
    result = await LLMAdapter().image_generation(
        provider_type=provider,
        base_url="http://head:8000/faden-x/v1",
        api_key_encrypted=None,
        litellm_provider=None,
        litellm_model="z-image-turbo",
        extra_params=None,
        payload={"prompt": "a servo motor", "size": "1216x832", "num_inference_steps": 9, "seed": 7},
    )
    assert result.status_code == 200
    assert sent["prompt"] == "a servo motor"
    assert sent["size"] == "1216x832"
    assert sent["extra_body"] == {"num_inference_steps": 9, "seed": 7}
    assert ("response_format" in sent) is asks_for_b64
