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
    """Do not silently truncate: every character of *text* is in a window.

    Words stay whole where they fit. One run without whitespace longer than a
    window (a long address, compact JSON, base64) is cut where a window ends;
    the rules (checksums, addresses) still read the whole text, only the model
    reads it in windows. Refused: a text needing more than *max_windows*
    windows, or a single character that does not fit one.
    """
    # Han, kana and Hangul have useful character boundaries even without spaces.
    # Keep Latin identifiers intact where they fit a window.
    cjk = "\\u3040-\\u30ff\\u3400-\\u9fff\\uac00-\\ud7af\\U00020000-\\U0003134f"
    words = [m.span() for m in re.finditer(rf"[{cjk}]|[^\s{cjk}]+", text)]
    if not words:
        return [Window(0, text, length(text))] if text else []
    output = []
    start = 0
    while start < len(words):
        end = min(start + 128, len(words))
        left = 0 if start == 0 else words[start][0]
        while True:
            right = len(text) if end == len(words) else words[end][0]
            value = text[left:right]
            tokens = length(value)
            if tokens <= max_tokens:
                break
            if end == start + 1:
                # One word alone does not fit: cut it where a window ends, and go
                # on with the rest of it.
                cut = _fitting(text, left, words[start], length, max_tokens)
                words[start : start + 1] = [
                    (words[start][0], cut),
                    (cut, words[start][1]),
                ]
                continue
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


def _fitting(
    text: str,
    left: int,
    word: tuple[int, int],
    length: Callable[[str], int],
    max_tokens: int,
) -> int:
    """Where to cut *word*: the furthest point in it where text[left:cut] fits."""
    low, high = word[0] + 1, word[1] - 1
    if low > high or length(text[left:low]) > max_tokens:
        raise PIIError("pii_input_too_large", 413)
    while low < high:
        middle = (low + high + 1) // 2
        if length(text[left:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return low
