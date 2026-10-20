"""Complete overlapping windows sized with the detector's real tokenizer."""

from dataclasses import dataclass
import re
from collections.abc import Callable

from faden_pii.services.pii.errors import PIIError


@dataclass(frozen=True)
class Window:
    offset: int
    text: str
    tokens: int


def windows(
    text: str,
    length: Callable[[str], int],
    *,
    max_tokens: int = 256,
    overlap: int = 24,
    max_windows: int = 64,
) -> list[Window]:
    """Do not silently truncate, including a single unencodable source word."""
    # Han, kana and Hangul have useful character boundaries even without spaces.
    # Keep Latin identifiers intact: slicing one could defeat checksum rules.
    cjk = "\\u3040-\\u30ff\\u3400-\\u9fff\\uac00-\\ud7af\\U00020000-\\U0003134f"
    words = list(re.finditer(rf"[{cjk}]|[^\s{cjk}]+", text))
    if not words:
        return [Window(0, text, length(text))] if text else []
    output = []
    start = 0
    while start < len(words):
        end = min(start + 128, len(words))
        left = 0 if start == 0 else words[start].start()
        while True:
            right = len(text) if end == len(words) else words[end].start()
            value = text[left:right]
            tokens = length(value)
            if tokens <= max_tokens:
                break
            if end == start + 1:
                raise PIIError("pii_input_too_large", 413)
            end = start + max(1, (end - start) // 2)
        output.append(Window(left, value, tokens))
        if len(output) > max_windows:
            raise PIIError("pii_input_too_large", 413)
        if end == len(words):
            break
        start = max(start + 1, end - min(overlap, (end - start) // 4))
    if (
        output[0].offset != 0
        or output[-1].offset + len(output[-1].text) != len(text)
        or any(a.offset + len(a.text) < b.offset for a, b in zip(output, output[1:]))
    ):
        raise PIIError("pii_incomplete_coverage", 503)
    return output
