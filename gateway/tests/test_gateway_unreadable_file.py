"""A file whose text could not be read is named in the chat (0.7.2 H.3).

Its extraction failed (or gave no text), so the model never saw it -- and nobody said so: the answer read as if
nothing had been attached. Now the model is told, and the chat hears of it as ``faden.attachment_left_out`` with
``reason: unreadable``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AttachmentScope, ChatAttachment, ExtractionStatus
from tests.test_gateway_chat_pictures import NO_VISION, _ask, _chat, _events, _model_answers, _system_text


async def _file(db: AsyncSession, sid: uuid.UUID, status: ExtractionStatus, text: str | None) -> None:
    db.add(ChatAttachment(
        tenant_id="tenant-a", user_id="user-1", session_id=sid, filename="scan.pdf", content_type="application/pdf",
        size_bytes=10, storage_key="k/scan.pdf", extracted_text=text, extraction_status=status,
        scope=AttachmentScope.SESSION,
    ))
    await db.commit()


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
@pytest.mark.parametrize(("status", "text"), [
    (ExtractionStatus.FAILED, None), (ExtractionStatus.COMPLETED, ""), (ExtractionStatus.COMPLETED, None),
], ids=["failed", "completed-empty", "completed-none"])
async def test_an_unreadable_file_is_named_to_the_model_and_the_chat(
    stream: bool, status: ExtractionStatus, text: str | None,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await _chat(fastapi_app, db_session, NO_VISION)
    await _file(db_session, sid, status, text)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "I could not read it.", sent)

    r = await _ask(client, sid, stream=stream, question="Summarise the file.")

    assert r.status_code == 200, r.text
    assert "[Attached file: scan.pdf -- not sent: its text could not be read.]" in _system_text(sent[0])
    [event] = _events(r, stream=stream)
    assert event["name"] == "faden.attachment_left_out"
    assert event["value"]["reason"] == "unreadable" and event["value"]["filename"] == "scan.pdf"
    assert event["value"]["message"] == "scan.pdf was not sent: its text could not be read."


@pytest.mark.anyio
async def test_a_file_still_being_read_is_not_called_unreadable(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid, _ = await _chat(fastapi_app, db_session, NO_VISION)
    await _file(db_session, sid, ExtractionStatus.PENDING, None)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await _ask(client, sid, question="Summarise the file.")

    assert r.status_code == 200, r.text
    assert "could not be read" not in _system_text(sent[0])
    assert _events(r, stream=False) == []
