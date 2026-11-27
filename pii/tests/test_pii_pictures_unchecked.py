"""A picture in a message goes on unchecked, counted; the text beside it is checked as ever.

0.7.2 G (release plan §6): the PII check refused every message with a picture
(``pii_unsupported_surface``), and the gateway answered "PII service unavailable".
The owner: tell the person instead of refusing, so the limit shows. The check reads
text only; a picture part passes as it came, and the response counts it.
"""

from types import SimpleNamespace

import pytest
from presidio_analyzer import RecognizerResult as Span
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.errors import PIIError
from faden_pii.services.pii.extraction import ExtractedPayload, is_picture
from faden_pii.services.pii.service import PIIService
from faden_pii.web.api.pii.schema import PIISanitizeResponse

PICTURE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}


def _flags_the_name(text: str) -> list[Span]:
    at = text.find("Anna Petersen")
    return [Span("PERSON", at, at + len("Anna Petersen"), 0.95)] if at >= 0 else []


def _service() -> PIIService:
    analyzer = SimpleNamespace(nlp_engine=SimpleNamespace(get_supported_languages=lambda: ["en"]))
    service = PIIService(analyzer, AnonymizerEngine())
    service._batch.analyze_iterator = lambda *, texts, **kw: [_flags_the_name(text) for text in texts]
    return service


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["redact", "tokenize"])
async def test_a_picture_goes_on_as_it_came_and_the_text_beside_it_is_checked(mode: str) -> None:
    message = {"role": "user", "content": [{"type": "text", "text": "Who is Anna Petersen?"}, PICTURE]}

    result = await _service().sanitize_payload({"messages": [message]}, mode=mode)

    parts = result.payload["messages"][0]["content"]
    assert parts[1] == PICTURE
    assert "Anna Petersen" not in parts[0]["text"]
    assert [e.entity_type for e in result.pii_report] == ["PERSON"]
    assert result.pictures_unchecked == 1
    assert message["content"][0]["text"] == "Who is Anna Petersen?"  # the caller's payload untouched


@pytest.mark.anyio
async def test_pictures_are_counted_across_messages_and_a_message_of_pictures_alone_passes() -> None:
    payload = {"messages": [
        {"role": "user", "content": [PICTURE, PICTURE]},
        {"role": "assistant", "content": "Two pictures."},
        {"role": "user", "content": [{"type": "text", "text": "And this?"}, PICTURE]},
    ]}

    result = await _service().sanitize_payload(payload, mode="redact")

    assert result.pictures_unchecked == 3
    assert result.payload["messages"][0]["content"] == [PICTURE, PICTURE]
    assert result.entities_found == 0


@pytest.mark.anyio
async def test_text_alone_counts_no_picture() -> None:
    result = await _service().sanitize_payload(
        {"messages": [{"role": "user", "content": "Anna Petersen"}]}, mode="redact",
    )
    assert result.pictures_unchecked == 0


@pytest.mark.parametrize("part", [
    {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}},
    {"type": "file", "file": {"file_data": "JVBERi0="}},
    {"type": "image_url", "image_url": "data:image/png;base64,iVBORw0KGgo="},  # not the chat shape
    {"type": "image_url"},
    {"type": "image_url", "image_url": {"url": 42}},
    {"type": "text", "text": 42},
    "a bare string",
])
def test_every_other_part_that_is_not_text_is_still_refused(part: object) -> None:
    with pytest.raises(PIIError) as raised:
        ExtractedPayload({"messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}, part]}]})
    assert raised.value.code == "pii_unsupported_surface"


def test_only_the_chat_shape_is_a_picture() -> None:
    assert is_picture(PICTURE)
    assert not is_picture({"type": "image_url", "image_url": {}})
    assert not is_picture({"type": "text", "text": "x"})
    assert not is_picture(None)


def test_the_response_says_how_many_went_unchecked_and_defaults_to_none() -> None:
    fields = {"sanitized_payload": {}, "entities_found": 0, "pii_report": []}
    assert PIISanitizeResponse(**fields).pictures_unchecked == 0
    assert PIISanitizeResponse(**fields, pictures_unchecked=2).model_dump()["pictures_unchecked"] == 2
