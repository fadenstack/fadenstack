"""A chat's pictures go only to a model that takes pictures.

The chat page uploads any file, and the gateway added the chat's pictures to
every turn, whatever its model: a model without vision could fail the turn.
A model the gateway knows does not take pictures now gets a note in their
place, and the client is told which picture was left out
(``faden.attachment_left_out``). A model that takes them, or one the gateway
knows nothing about, gets them as before.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.models.gateway import (
    AttachmentScope,
    ChatAttachment,
    ExtractionStatus,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    ProviderType,
)
from tests.test_faden_events import EVENTS, _faden
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _model_answers,
    _Observability,
    _seed,
    _session,
    _token,
)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
NO_VISION = {"task": "chat", "vision": False}
VISION = {"task": "chat", "vision": True}
NOT_SAID = {"task": "chat"}
NOTE = f"cat.png was not sent: {ALIAS} does not take pictures. Choose a model that does."
TEXT = "The Lisbon office closes on 30 June 2027."


class _Store:
    """The chat's file store: every file is the same small picture."""

    def __init__(self) -> None:
        self.read: list[str] = []

    async def get_bytes(self, key: str) -> bytes:
        self.read.append(key)
        return PNG


async def _chat(fastapi_app: FastAPI, db: AsyncSession, node_metadata: dict[str, Any]) -> tuple[uuid.UUID, _Store]:
    await _seed(db, provider=ProviderType.VLLM, pii=None, node_metadata=node_metadata)
    fastapi_app.state.gateway_observability = _Observability()
    store = _Store()
    fastapi_app.state.chat_file_store = store
    return await _session(db), store


async def _picture(db: AsyncSession, sid: uuid.UUID, filename: str = "cat.png") -> None:
    db.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename=filename, content_type="image/png",
        size_bytes=len(PNG), storage_key=f"k/{filename}", extraction_status=ExtractionStatus.SKIPPED,
        scope=AttachmentScope.SESSION,
    ))
    await db.commit()


async def _text_file(db: AsyncSession, sid: uuid.UUID) -> None:
    db.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename="note.txt", content_type="text/plain",
        size_bytes=len(TEXT), storage_key="k/note.txt", extracted_text=TEXT,
        extraction_status=ExtractionStatus.COMPLETED, scope=AttachmentScope.SESSION,
    ))
    await db.commit()


async def _ask(
    client: AsyncClient, sid: uuid.UUID, *, stream: bool = False, question: str = "What is in the picture?",
) -> Any:
    return await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS}, json={
        "model": ALIAS, "session_id": str(sid), "stream": stream, "messages": [{"role": "user", "content": question}],
    })


def _image_urls(sent: dict[str, Any]) -> list[str]:
    return [
        p["image_url"]["url"]
        for m in sent["messages"] if isinstance(m["content"], list)
        for p in m["content"] if p.get("type") == "image_url"
    ]


def _system_text(sent: dict[str, Any]) -> str:
    return "\n".join(str(m["content"]) for m in sent["messages"] if m["role"] == "system")


def _events(r: Any, *, stream: bool) -> list[dict[str, Any]]:
    """The Faden events of an answer, the tool mode notice aside (test_tool_modes)."""
    if stream:
        return _faden(r.text)
    return [e for e in r.json()["faden"]["events"] if e.get("name") != "faden.tool_mode"]


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_picture_is_not_sent_to_a_model_that_does_not_take_pictures(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, store = await _chat(fastapi_app, db_session, NO_VISION)
    await _picture(db_session, sid)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "I cannot see it.", sent)

    r = await _ask(client, sid, stream=stream)

    assert r.status_code == 200, r.text
    assert _image_urls(sent[0]) == []
    assert store.read == [], "the picture is not even read"
    assert "[Attached image: cat.png -- not sent: this model does not take pictures.]" in _system_text(sent[0]), (
        "the model is told, and does not answer as if nothing had been attached"
    )
    [event] = _events(r, stream=stream)
    assert event["type"] == "CUSTOM" and event["name"] == "faden.attachment_left_out"
    value = event["value"]
    assert uuid.UUID(value.pop("attachmentId"))
    assert value == {"filename": "cat.png", "model": ALIAS, "reason": "no_vision", "message": NOTE}
    if stream:
        assert r.text.index("event: faden") < r.text.index('"I ca"'), "told before the answer"


@pytest.mark.anyio
@pytest.mark.parametrize("node_metadata", [VISION, NOT_SAID], ids=["takes-pictures", "not-known"])
async def test_a_model_that_takes_pictures_or_may_gets_them_as_before(
    node_metadata: dict[str, Any],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, store = await _chat(fastapi_app, db_session, node_metadata)
    await _picture(db_session, sid)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "A cat.", sent)

    r = await _ask(client, sid)

    assert r.status_code == 200, r.text
    assert _image_urls(sent[0]) == [DATA_URL]
    assert store.read == ["k/cat.png"]
    assert "not sent" not in _system_text(sent[0])
    assert _events(r, stream=False) == []


@pytest.mark.anyio
async def test_a_text_file_still_goes_to_a_model_that_does_not_take_pictures(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await _chat(fastapi_app, db_session, NO_VISION)
    await _text_file(db_session, sid)
    await _picture(db_session, sid)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "On 30 June 2027.", sent)

    r = await _ask(client, sid, question="When does the Lisbon office close?")

    assert r.status_code == 200, r.text
    assert f"[Attached: note.txt]\n{TEXT}" in _system_text(sent[0])
    assert [e["value"]["filename"] for e in _events(r, stream=False)] == ["cat.png"], "only the picture is reported"


@pytest.mark.anyio
async def test_the_model_is_looked_up_only_for_a_chat_with_pictures_and_once_a_turn(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await _chat(fastapi_app, db_session, NO_VISION)
    asked: list[str] = []
    lookup = GatewayDAO.alias_vision

    async def counted(self: GatewayDAO, alias: str) -> bool | None:
        asked.append(alias)
        return await lookup(self, alias)

    monkeypatch.setattr(GatewayDAO, "alias_vision", counted)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    assert (await _ask(client, sid, question="Hello")).status_code == 200
    await _text_file(db_session, sid)
    assert (await _ask(client, sid, question="What does the note say?")).status_code == 200
    assert asked == [], "a chat without pictures costs no lookup"

    await _picture(db_session, sid, "cat.png")
    await _picture(db_session, sid, "dog.png")
    r = await _ask(client, sid)
    assert r.status_code == 200, r.text
    assert asked == [ALIAS], "one lookup for the turn, however many pictures"
    assert sorted(e["value"]["filename"] for e in _events(r, stream=False)) == ["cat.png", "dog.png"]


@pytest.mark.anyio
async def test_a_client_that_sends_its_own_picture_is_not_affected(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the chat's attached pictures are held back; a request's own parts go on as the client sent them."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=NO_VISION)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What is in the picture?"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]}],
    })

    assert r.status_code == 200, r.text
    assert _image_urls(sent[0]) == [DATA_URL]
    assert _events(r, stream=False) == []


# ── The model list ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_model_list_says_whether_a_model_takes_pictures(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=NO_VISION)
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
    [model] = r.json()["data"]
    assert (model["id"], model["kind"], model["vision"]) == (ALIAS, "chat", False)
    assert {"object", "created", "owned_by"} <= model.keys(), "the OpenAI fields stay"


@pytest.mark.anyio
async def test_vision_is_read_for_each_alias_in_one_go(db_session: AsyncSession) -> None:
    """As for one alias: false when one route says no, true when all say yes, else not known."""

    async def route(alias: str, *said: tuple[dict[str, Any] | None, dict[str, Any] | None]) -> None:
        db_session.add(LLMModelAlias(alias=alias, enabled=True))
        await db_session.flush()
        for metadata, capabilities in said:
            instance = LLMProviderInstance(
                id=uuid.uuid4(), type=ProviderType.VLLM, base_url=f"http://{alias}:8000", enabled=True,
                node_metadata=metadata, capabilities=capabilities,
            )
            db_session.add(instance)
            await db_session.flush()
            db_session.add(LLMPoolMembership(model_alias=alias, provider_instance_id=instance.id, enabled=True))
        await db_session.flush()

    await route("sees", ({"vision": True}, None), (None, {"vision": True}))
    await route("one-does-not", ({"vision": True}, None), ({"vision": False}, None))
    await route("one-says-nothing", ({"vision": True}, None), ({"task": "chat"}, None))

    dao = GatewayDAO(db_session)
    aliases = ["sees", "one-does-not", "one-says-nothing", "no-routes"]
    expected = {"sees": True, "one-does-not": False, "one-says-nothing": None, "no-routes": None}
    assert await dao.alias_visions(aliases) == expected
    assert {alias: await dao.alias_vision(alias) for alias in aliases} == expected
    assert await dao.alias_visions([]) == {}
