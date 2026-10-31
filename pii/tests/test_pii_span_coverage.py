"""Accepted sensitive coverage survives transformation, independently of a model."""

from types import SimpleNamespace

import pytest
from presidio_analyzer import RecognizerResult as Span
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.service import PIIService
from faden_pii.services.pii.spans import resolve_spans


def make_service(rows):
    analyzer = SimpleNamespace(
        nlp_engine=SimpleNamespace(get_supported_languages=lambda: ["en"])
    )
    service = PIIService(analyzer, AnonymizerEngine())
    service._batch.analyze_iterator = lambda *, texts, **kw: [
        rows(text) for text in texts
    ]
    return service


@pytest.mark.anyio
@pytest.mark.parametrize(
    "name",
    [
        "Hans Müller",
        "Özlem Yılmaz",
        "François de La Tour",
        "José de la Cruz",
        "James Wilson",
        "Grace Lee",
    ],
)
async def test_nested_name_is_fully_hidden_and_restorable(name):
    service = make_service(
        lambda t: [
            Span("PERSON", 2, len(t), 0.7),
            Span("PERSON", t.rindex(" ") + 1, len(t), 0.95),
        ]
    )
    text = "😀 " + name
    result = await service.sanitize_payload(
        {"messages": [{"content": text}]}, mode="tokenize"
    )
    masked = result.payload["messages"][0]["content"]
    assert masked == "😀 [PERSON_1]"
    assert result.token_mapping == {"[PERSON_1]": name}
    assert masked.replace("[PERSON_1]", name) == text


def test_crossing_spans_are_unioned_but_adjacent_people_stay_distinct():
    rows = resolve_spans(
        "abcdefghij",
        [
            Span("PERSON", 0, 4, 0.9),
            Span("LOCATION", 2, 6, 0.8),
            Span("PERSON", 6, 10, 0.9),
        ],
    )
    assert [(r.start, r.end) for r in rows] == [(0, 6), (6, 10)]


def test_sdk_token_does_not_discard_sensitive_text_around_it():
    text = "Anna [[fs:PERSON_1]] Müller"
    rows = resolve_spans(text, [Span("PERSON", 0, len(text), 0.9)])
    assert [(text[r.start : r.end]) for r in rows] == ["Anna ", " Müller"]


@pytest.mark.anyio
async def test_tool_json_is_rebuilt_without_changing_types_or_keys():
    service = make_service(
        lambda t: [Span("PERSON", 0, len(t), 0.9)] if t == 'Jörg "Weiß"' else []
    )
    # Produce valid escaped JSON without relying on literal quoting in the fixture.
    import json

    original = json.dumps({"who": 'Jörg "Weiß"', "count": 7, "enabled": True})
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "send", "arguments": original}}],
            }
        ]
    }
    result = await service.sanitize_payload(payload, mode="tokenize")
    args = json.loads(
        result.payload["messages"][0]["tool_calls"][0]["function"]["arguments"]
    )
    assert args == {"who": "[PERSON_1]", "count": 7, "enabled": True}
    assert payload["messages"][0]["tool_calls"][0]["function"]["arguments"] == original
    assert result.pii_report[0].path.endswith("/arguments/who")


@pytest.mark.anyio
async def test_empty_result_with_disabled_cache_is_valid():
    service = make_service(lambda t: [])
    service._analyses._size = 0
    assert not (await service.scan("nothing sensitive")).has_pii


@pytest.mark.anyio
async def test_conversation_mapping_masks_short_names_without_polluting_cache():
    service = make_service(lambda text: [])
    first = await service.sanitize_payload(
        {"input": "Anna"}, mode="tokenize", token_mapping={"[PERSON_1]": "Anna"}
    )
    assert first.payload["input"] == "[PERSON_1]"
    # A different conversation does not inherit the known name.
    second = await service.sanitize_payload({"input": "Anna"}, mode="tokenize")
    assert second.payload["input"] == "Anna"
    await service.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key", ["first_name", "Vorname", "prénom", "nombre_de_pila", "apellido"]
)
async def test_explicit_name_fields_protect_short_values_without_a_model_hit(key):
    import json

    service = make_service(lambda text: [])
    args = json.dumps({key: "Jörg", "file_name": "Report", "count": 7})
    payload = {
        "messages": [
            {"tool_calls": [{"function": {"name": "send", "arguments": args}}]}
        ]
    }
    try:
        result = await service.sanitize_payload(payload, mode="tokenize")
        value = json.loads(
            result.payload["messages"][0]["tool_calls"][0]["function"]["arguments"]
        )
        assert value == {key: "[PERSON_1]", "file_name": "Report", "count": 7}
        assert result.token_mapping == {"[PERSON_1]": "Jörg"}
    finally:
        await service.close()


@pytest.mark.anyio
async def test_name_question_masks_only_target_and_does_not_change_cached_bare_analysis():
    service = make_service(lambda text: [])
    try:
        result = await service.sanitize_payload(
            {
                "messages": [
                    {"role": "assistant", "content": "Wie lautet dein Vorname?"},
                    {"role": "user", "content": "Jörg"},
                ]
            },
            mode="tokenize",
        )
        assert result.payload["messages"][1]["content"] == "[PERSON_1]"
        assert result.payload["messages"][0]["content"] == "Wie lautet dein Vorname?"
        plain = await service.sanitize_payload({"input": "Jörg"}, mode="tokenize")
        assert plain.payload["input"] == "Jörg"
    finally:
        await service.close()


@pytest.mark.anyio
async def test_name_question_masks_the_name_in_a_message_of_parts():
    """The gateway puts a turn's skills in a part before the person's words."""
    service = make_service(lambda text: [])
    skill = "=== ACTIVE SKILL: Terse (v1) ===\nAnswer in five words.\n=== END SKILL ==="
    try:
        result = await service.sanitize_payload(
            {
                "messages": [
                    {"role": "assistant", "content": "Wie lautet dein Vorname?"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": skill},
                            {"type": "text", "text": "Jörg"},
                        ],
                    },
                ]
            },
            mode="tokenize",
        )
        parts = result.payload["messages"][1]["content"]
        assert [p["text"] for p in parts] == [skill, "[PERSON_1]"]
        assert result.token_mapping == {"[PERSON_1]": "Jörg"}
    finally:
        await service.close()
