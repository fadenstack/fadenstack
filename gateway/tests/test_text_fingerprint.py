"""The gateway fingerprints an answer's text exactly as the provenance engine does.

The vectors are the engine's (``contracts/v1/text-fingerprint-vectors.json``), copied into ``tests/data``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from faden_gateway.services.gateway import text_fingerprint

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "data" / "text-fingerprint-vectors.json").read_text(encoding="utf-8"),
)


def test_every_vector_of_the_engine_is_here() -> None:
    assert len(VECTORS) == 6
    assert all(set(v) == {"input", "words", "phrase_count", "fingerprint"} for v in VECTORS)


@pytest.mark.parametrize("vector", VECTORS, ids=[f"vector-{n}" for n in range(len(VECTORS))])
def test_the_text_is_fingerprinted_as_the_engine_does(vector: dict[str, Any]) -> None:
    assert text_fingerprint.words(vector["input"]) == vector["words"]
    made = text_fingerprint.fingerprint(vector["input"])
    assert (made[0] if made else None) == vector["fingerprint"]
    if made:
        assert made[1] == vector["phrase_count"]


def test_the_answer_variety_and_an_invisible_mark_change_nothing() -> None:
    text = VECTORS[0]["input"]
    varied = text + chr(0x2060) + chr(0x200B) * 3 + chr(0xFEFF) + chr(0xFE01) + chr(0xE0110)
    assert text_fingerprint.fingerprint(varied) == text_fingerprint.fingerprint(text)


def test_a_tool_calls_json_arguments_give_the_words_they_place() -> None:
    placed = "First line of the note about the pumps.\nSecond line says the seals are due next week."
    arguments = json.dumps({"text": placed})
    assert text_fingerprint.words(arguments)[1:] == text_fingerprint.words(placed)
