"""The gateway hashes an answer's text exactly as the provenance engine and the SDKs do.

The vectors are the engine's (``contracts/v1/text-hash-vectors.json``), copied into ``tests/data``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from faden_gateway.services.gateway.content_hash import normalize_text, text_sha256

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "data" / "text-hash-vectors.json").read_text(encoding="utf-8"),
)


def test_every_vector_of_the_engine_is_here() -> None:
    assert len(VECTORS) == 8
    assert all(set(v) == {"input", "normalized", "sha256"} for v in VECTORS)


@pytest.mark.parametrize("vector", VECTORS, ids=[f"vector-{n}" for n in range(len(VECTORS))])
def test_the_text_is_normalised_and_hashed_as_the_engine_does(vector: dict[str, Any]) -> None:
    assert normalize_text(vector["input"]) == vector["normalized"]
    assert text_sha256(vector["input"]) == vector["sha256"]


def test_a_reflowed_text_hashes_the_same() -> None:
    reflowed = "The pump" + chr(0xA0) + "P-200\r\n\r\nis due." + chr(0x07)
    assert text_sha256(reflowed) == text_sha256("The pump P-200 is due.")
