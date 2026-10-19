r"""How the gateway fingerprints an answer's text for its receipt: exactly as the provenance engine does.

The receipt's hash finds a text only as it was generated; the fingerprint finds it edited too (words taken
out, sentences added), and backs up the model's watermark, which some runtimes put in weakly (dual-key Gumbel
with MTP) and remote models not at all. It is a sample of the text's five-word phrases as hashes, never the
text: the engine keeps each hash only keyed with its hash key (its docs/api.md, "Fingerprints").

A text's phrases: JSON string escapes (``\n``, ``\t``, ``\r``, ``\"``, ``\\``, ``\/``, ``\uXXXX``) read as
what they stand for, as tool calls' arguments arrive JSON-encoded; Unicode NFKC, case-folded, without format
characters (zero-width spaces, word joiners, U+FEFF, soft hyphens) and variation selectors, so the gateway's
answer variety and invisible marks change nothing; its words (``\w+``); every five consecutive words joined
by one space, as the first 8 bytes of their SHA-256, big-endian. The fingerprint is the 64 smallest distinct
hashes, 16 hex digits each, with the number of distinct phrases; a text of fewer than 20 phrases has none.

Tested against the engine's ``contracts/v1/text-fingerprint-vectors.json`` (a copy in ``tests/data``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

#: Words in one phrase.
PHRASE_WORDS = 5
#: Phrase hashes in a fingerprint.
SAMPLE = 64
#: Fewer distinct phrases than this, and a text gets no fingerprint.
MIN_PHRASES = 20
#: Phrases read of one text at most.
MAX_PHRASES = 20_000

_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|([nrt])|([\"\\/]))")
_WORD = re.compile(r"\w+")
#: Variation selectors (U+FE00-U+FE0F, U+E0100-U+E01EF).
_SELECTORS = re.compile("[\ufe00-\ufe0f\U000e0100-\U000e01ef]")


def _unescape(found: re.Match[str]) -> str:
    code, control, literal = found.groups()
    if code is not None:
        return chr(int(code, 16))
    return " " if control is not None else literal


def words(text: str) -> list[str]:
    """The text's words as phrases are made of them."""
    plain = unicodedata.normalize("NFKC", _ESCAPE.sub(_unescape, text))
    plain = _SELECTORS.sub("", "".join(c for c in plain if unicodedata.category(c) != "Cf"))
    return _WORD.findall(plain.casefold())


def phrase_hashes(text: str) -> set[int]:
    """The distinct hashes of the text's five-word phrases, the first :data:`MAX_PHRASES` of them."""
    found = words(text)
    hashes: set[int] = set()
    for start in range(min(len(found) - PHRASE_WORDS + 1, MAX_PHRASES)):
        phrase = " ".join(found[start : start + PHRASE_WORDS])
        hashes.add(int.from_bytes(hashlib.sha256(phrase.encode("utf-8")).digest()[:8], "big"))
    return hashes


def fingerprint(text: str) -> tuple[list[str], int] | None:
    """The text's fingerprint: its :data:`SAMPLE` smallest phrase hashes (hex) and its phrases' number."""
    hashes = phrase_hashes(text)
    if len(hashes) < MIN_PHRASES:
        return None
    return [f"{value:016x}" for value in sorted(hashes)[:SAMPLE]], len(hashes)
