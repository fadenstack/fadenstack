"""How the gateway hashes an answer's text for its receipt: exactly as the provenance engine does.

The engine never receives content (its docs/api.md, "Hashing content"): the gateway sends the SHA-256 of
the text. The text is normalised first, so a host that reflows or re-spaces it (Word, Outlook, a web
editor) still matches what an SDK hashes when it places the text: Unicode NFC; U+0007 and U+0001 removed
(Word's cell and field marks); every run of white space (spaces, tabs, line breaks, U+00A0 and the other
Unicode spaces) replaced by one space; leading and trailing space removed. Then UTF-8, SHA-256, hex.

Tested against the engine's ``contracts/v1/text-hash-vectors.json`` (a copy in ``tests/data``).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_MARKS = str.maketrans("", "", "\x07\x01")
# \s covers ASCII white space and the Unicode spaces
# (U+00A0, U+2000-U+200A, U+2028, U+2029, U+202F, U+205F, U+3000).
_SPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """The text as it is hashed."""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text).translate(_MARKS)).strip()


def text_sha256(text: str) -> str:
    """SHA-256 of the normalised text, hex."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
