"""A page agent's element refs in a tool result are never redacted.

Tracker 1141, release plan §12.2 C2. Live on a test server (12 Nov): the PII model took
the browser agent's element refs for places, the page's element listing reached the
model as ``[<LOCATION>]`` instead of ``[e50]``, ``fill_field`` got ``ref: <LOCATION>``,
and the agent looped until its run's time was over. A ref is structure: in a tool
result it stays as it is, in either mode, whatever found it; the personal data around
it is still masked.
"""

from types import SimpleNamespace

import pytest
from presidio_analyzer import RecognizerResult as Span
from presidio_anonymizer import AnonymizerEngine

from faden_pii.services.pii.service import PIIService
from faden_pii.services.pii.spans import STRUCTURAL_REF, subtract

LISTING = '[e50] textbox "Name"\n[f1.e3] button "Anna Petersen"'


def _flags_refs_and_the_name(text: str) -> list[Span]:
    """What the model did: each ref's inside a place, and the name a person."""
    found = []
    for ref in ("e50", "f1.e3"):
        at = text.find(ref)
        if at >= 0:
            found.append(Span("LOCATION", at, at + len(ref), 0.85))
    at = text.find("Anna Petersen")
    if at >= 0:
        found.append(Span("PERSON", at, at + len("Anna Petersen"), 0.95))
    return found


def _service(rows=_flags_refs_and_the_name) -> PIIService:
    analyzer = SimpleNamespace(
        nlp_engine=SimpleNamespace(get_supported_languages=lambda: ["en"])
    )
    service = PIIService(analyzer, AnonymizerEngine())
    service._batch.analyze_iterator = lambda *, texts, **kw: [
        rows(text) for text in texts
    ]
    return service


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["tool", "function"])
async def test_a_tool_results_refs_stay_and_the_name_is_redacted(role: str) -> None:
    result = await _service().sanitize_payload(
        {"messages": [{"role": role, "tool_call_id": "c1", "content": LISTING}]},
        mode="redact",
    )

    assert (
        result.payload["messages"][0]["content"]
        == '[e50] textbox "Name"\n[f1.e3] button "<PERSON>"'
    )
    assert [e.entity_type for e in result.pii_report] == ["PERSON"]


@pytest.mark.anyio
async def test_in_tokenize_mode_too() -> None:
    result = await _service().sanitize_payload(
        {"messages": [{"role": "tool", "tool_call_id": "c1", "content": LISTING}]},
        mode="tokenize",
    )

    assert (
        result.payload["messages"][0]["content"]
        == '[e50] textbox "Name"\n[f1.e3] button "[PERSON_1]"'
    )
    assert result.token_mapping == {"[PERSON_1]": "Anna Petersen"}


@pytest.mark.anyio
async def test_a_tool_result_in_parts_keeps_its_refs() -> None:
    message = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": [{"type": "text", "text": LISTING}],
    }
    result = await _service().sanitize_payload({"messages": [message]}, mode="redact")

    assert result.payload["messages"][0]["content"][0]["text"].startswith(
        '[e50] textbox "Name"\n[f1.e3]'
    )


@pytest.mark.anyio
async def test_a_value_tokenized_earlier_does_not_take_a_ref() -> None:
    """A mapping that holds "e50" (from an earlier turn) masks it as a value, not a ref."""
    result = await _service(lambda text: []).sanitize_payload(
        {
            "messages": [
                {"role": "tool", "tool_call_id": "c1", "content": "[e50] is e50"}
            ]
        },
        mode="tokenize",
        token_mapping={"[LOCATION_1]": "e50"},
    )

    assert result.payload["messages"][0]["content"] == "[e50] is [LOCATION_1]"


@pytest.mark.anyio
async def test_a_span_over_a_ref_keeps_the_personal_text_around_it() -> None:
    def whole_line(text: str) -> list[Span]:
        at = text.find("e7] Anna")
        return [Span("PERSON", at, at + len("e7] Anna"), 0.9)] if at >= 0 else []

    result = await _service(whole_line).sanitize_payload(
        {"messages": [{"role": "tool", "tool_call_id": "c1", "content": "[e7] Anna"}]},
        mode="redact",
    )

    assert result.payload["messages"][0]["content"] == "[e7] <PERSON>"


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["user", "assistant", "system"])
async def test_only_tool_results_are_spared(role: str) -> None:
    """What a person or the model writes is scanned as before: the rule is for tools."""
    result = await _service().sanitize_payload(
        {"messages": [{"role": role, "content": LISTING}]},
        mode="redact",
    )

    assert "[e50]" not in result.payload["messages"][0]["content"]


def test_the_ref_pattern() -> None:
    assert [
        m.group() for m in STRUCTURAL_REF.finditer("[e1] [e50] [f1.e3] [f12.e345]")
    ] == [
        "[e1]",
        "[e50]",
        "[f1.e3]",
        "[f12.e345]",
    ]
    assert not STRUCTURAL_REF.search("[e] [x50] [f1e3] [E50] e50 [e5a]")


def test_subtract_keeps_what_is_around_a_ref() -> None:
    text = "Anna [e2] Petersen"
    rows = subtract(text, [Span("PERSON", 0, len(text), 0.9)], STRUCTURAL_REF)

    assert [text[r.start : r.end] for r in rows] == ["Anna ", " Petersen"]
