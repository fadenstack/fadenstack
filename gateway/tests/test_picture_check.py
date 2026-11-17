"""Pictures leave by what they are, and no multimodal handle leaves (release plan §12.3, vLLM 0.31.0 advisories).

GHSA-h3rc-j82p-2c3h: vLLM opens a picture by its own first bytes, so an EPS file labelled ``image/png`` reaches
Ghostscript where the image has it. GHSA-6757-hggm-hp2w: a forged multimodal shared-memory handle reaches
``pickle.loads`` on a worker. The gateway lets through PNG, JPEG, WebP and GIF only, and drops what names a
multimodal item held elsewhere.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import picture_check
from faden_gateway.services.gateway.errors import GatewayError
from tests.test_agent_routes import URL as AGENT_URL
from tests.test_agent_routes import _chat as _agent_chat
from tests.test_agent_routes import _headers as _agent_headers
from tests.test_agent_routes import _model as _agent_model
from tests.test_agent_routes import backend  # noqa: F401 - the fixture the agent route needs
from tests.test_gateway_pipeline_combined import ALIAS, _model_answers, _Observability, _seed, _token

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00"
EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 1 1\n"
EPS_BINARY = b"\xc5\xd0\xd3\xc6\x20\x00\x00\x00"


def _data(raw: bytes, label: str = "image/png") -> str:
    return f"data:{label};base64," + base64.b64encode(raw).decode()


def _payload(*parts: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "Look"}, *parts]}],
            **extra}


def _refusal(payload: dict[str, Any]) -> GatewayError:
    with pytest.raises(GatewayError) as refused:
        picture_check.checked(payload)
    assert refused.value.status_code == 400
    return refused.value


@pytest.mark.parametrize(("raw", "kind"), [(PNG, "PNG"), (JPEG, "JPEG"), (WEBP, "WebP"), (GIF, "GIF")])
def test_the_four_formats_pass_by_their_first_bytes_whatever_their_label(raw: bytes, kind: str) -> None:
    assert picture_check.kind_of(raw) == kind
    payload = _payload({"type": "image_url", "image_url": {"url": _data(raw, "image/x-whatever")}})
    assert picture_check.checked(payload) is payload, "nothing to drop: the payload itself"


@pytest.mark.parametrize(
    ("raw", "named"),
    [(EPS, "PostScript (EPS)"), (EPS_BINARY, "PostScript (EPS)"), (b"%PDF-1.7\n", "PDF"), (b"BM6\x00\x00\x00", "BMP"),
     (b"II*\x00\x08\x00", "TIFF"), (b"<svg xmlns='x'/>", "SVG or XML"), (b"hello world", "not a picture")],
)
def test_anything_else_labelled_a_picture_is_refused(raw: bytes, named: str) -> None:
    refused = _refusal(_payload({"type": "image_url", "image_url": {"url": _data(raw, "image/png")}}))

    assert refused.code == "picture_type_not_allowed"
    assert refused.param == "messages[0].content[1]"
    assert named in refused.message and "PNG, JPEG, WebP and GIF" in refused.message


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_image", "image_url": _data(EPS)},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(EPS).decode()}},
        {"image_url": {"url": _data(EPS)}},
        {"type": "image_url", "image_url": {"url": "data:image/png," + "%25%21PS-Adobe-3.0"}},
    ],
    ids=["responses", "anthropic", "no-type", "not-base64"],
)
def test_every_shape_of_a_picture_part_is_checked(part: dict[str, Any]) -> None:
    assert _refusal(_payload(part)).code == "picture_type_not_allowed"


def test_a_picture_in_any_message_is_checked_and_a_good_one_in_parts_and_alone() -> None:
    payload = {"model": "m", "messages": [
        {"role": "user", "content": {"type": "image_url", "image_url": {"url": _data(PNG)}}},
        {"role": "tool", "tool_call_id": "c", "content": [{"type": "image_url", "image_url": {"url": _data(EPS)}}]},
    ]}
    assert _refusal(payload).param == "messages[1].content[0]"


@pytest.mark.parametrize("part", [
    {"type": "image_url", "image_url": None, "uuid": "sha256:deadbeef"},
    {"type": "image_url", "image_url": {"url": ""}},
    {"type": "image_url", "uuid": "a-cached-item"},
])
def test_a_picture_part_without_data_is_refused(part: dict[str, Any]) -> None:
    assert _refusal(_payload(part)).code == "picture_missing"


def test_a_parts_uuid_and_the_handle_fields_are_dropped_and_the_clients_objects_kept() -> None:
    picture = {"type": "image_url", "image_url": {"url": _data(PNG)}, "uuid": "another-requests-item"}
    sound = {"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "wav"}, "uuid": "x"}
    payload = _payload(picture, sound, features={"kwargs_data": "forged"}, mm_hashes={"image": ["h"]},
                       kwargs_data="x", multi_modal_uuids={"image": ["u"]}, temperature=0.2)

    out = picture_check.checked(payload)

    assert out["messages"][0]["content"][1] == {"type": "image_url", "image_url": {"url": _data(PNG)}}
    assert "uuid" not in out["messages"][0]["content"][2]
    assert picture_check.HANDLE_FIELDS.isdisjoint(out)
    assert out["temperature"] == 0.2 and out["model"] == "m"
    assert picture["uuid"] == "another-requests-item" and "features" in payload, "the client's objects unchanged"


@pytest.mark.parametrize("kind", ["image_embeds", "audio_embeds"])
def test_embeddings_as_input_are_refused(kind: str) -> None:
    refused = _refusal(_payload({"type": kind, kind: base64.b64encode(b"PK\x03\x04torch").decode()}))
    assert refused.code == "embeds_not_allowed"


def test_text_and_other_media_pass_as_they_are() -> None:
    payload = _payload({"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "wav"}},
                       {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AAAAIGZ0eXA="}})
    assert picture_check.checked(payload) is payload
    plain = {"model": "m", "messages": [{"role": "user", "content": "Hello"}]}
    assert picture_check.checked(plain) is plain


def test_with_the_url_check_off_a_picture_url_goes_on_unchecked() -> None:
    payload = _payload({"type": "image_url", "image_url": {"url": "https://pictures.example.com/cat.png"}})
    assert picture_check.checked(payload) is payload


# ── the route ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_an_eps_labelled_png_is_refused_before_the_model_is_asked(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": _data(EPS)}},
        ]}],
    })

    assert r.status_code == 400, r.text
    error = r.json()["error"]
    assert error["code"] == "picture_type_not_allowed"
    assert error["param"] == "messages[0].content[1]"
    assert sent == [], "the model was not asked"


@pytest.mark.anyio
async def test_the_agent_route_names_the_apps_own_position(
    client: AsyncClient, db_session: AsyncSession, backend: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The agent's instructions go first: a refusal still names the place in the app's own messages (found on .172)."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _agent_model(monkeypatch, sent)

    r = await client.post(AGENT_URL, headers=_agent_headers(), json=_agent_chat(messages=[
        {"role": "user", "content": [{"type": "text", "text": "What is this?"},
                                     {"type": "image_url", "image_url": {"url": _data(EPS)}}]},
    ]))

    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "picture_type_not_allowed"
    assert r.json()["error"]["param"] == "messages[0].content[1]"
    assert sent == []


@pytest.mark.anyio
async def test_a_good_picture_reaches_the_model_without_its_uuid_or_handle_fields(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "a cat", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "features": {"kwargs_data": {"address": 1, "monotonic_id": 2}}, "mm_hashes": {"image": ["h"]},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": _data(JPEG, "image/jpeg")}, "uuid": "someone-elses"},
        ]}],
    })

    assert r.status_code == 200, r.text
    assert sent, "the model was asked"
    assert picture_check.HANDLE_FIELDS.isdisjoint(sent[0])
    pictures = [p for m in sent[0]["messages"] if isinstance(m["content"], list) for p in m["content"]
                if p.get("type") == "image_url"]
    assert pictures == [{"type": "image_url", "image_url": {"url": _data(JPEG, "image/jpeg")}}]
