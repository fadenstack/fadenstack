"""Resolve accepted sensitive coverage independently of model confidence."""

import math
import re
from numbers import Real

from presidio_analyzer import RecognizerResult

LOCAL_TOKEN = re.compile(r"\[\[fs:[A-Z][A-Z0-9_]*_\d+\]\]")
#: A page agent's element refs in a tool result (``[e50]``, ``[f1.e3]``: element 50,
#: element 3 of frame 1). Structure, never personal data: redacted, the agent could not
#: name the element it acts on and looped (tracker 1141, release plan §12.2 C2).
STRUCTURAL_REF = re.compile(r"\[(?:f\d+\.)?e\d+\]")


def subtract(
    text: str,
    results: list[RecognizerResult],
    pattern: re.Pattern[str],
    *,
    trim: bool = False,
) -> list[RecognizerResult]:
    """*results* without what *pattern* matches in *text*.

    A span over a match keeps the text around it. With *trim*, a piece cut at a match
    loses the white space at its ends (``[e7] <PERSON>``, not ``[e7]<PERSON>``); a piece
    of white space only is dropped.
    """
    protected = [m.span() for m in pattern.finditer(text)]
    if not protected:
        return results
    kept: list[RecognizerResult] = []
    for result in results:
        pieces = [(result.start, result.end)]
        for left, right in protected:
            pieces = [
                (a, b)
                for start, end in pieces
                for a, b in [(start, min(end, left)), (max(start, right), end)]
                if a < b
            ]
        if trim and pieces != [(result.start, result.end)]:
            pieces = [_trimmed(text, a, b) for a, b in pieces]
            pieces = [(a, b) for a, b in pieces if a < b]
        kept.extend(
            RecognizerResult(result.entity_type, a, b, result.score) for a, b in pieces
        )
    return kept


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def validate_spans(text: str, results: list[RecognizerResult]) -> None:
    """Validate even candidates that will subsequently fall below a threshold."""
    from faden_pii.services.pii.errors import PIIError

    for result in results:
        if (
            type(result.start) is not int
            or type(result.end) is not int
            or not 0 <= result.start < result.end <= len(text)
            or not isinstance(result.entity_type, str)
            or not result.entity_type
            or not isinstance(result.score, Real)
            or isinstance(result.score, bool)
            or not math.isfinite(result.score)
            or not 0 <= result.score <= 1
        ):
            raise PIIError("pii_invalid_detector_result", 503)


def resolve_spans(text: str, results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Union intersecting spans, subtracting existing SDK placeholders only."""
    validate_spans(text, results)
    accepted = subtract(text, results, LOCAL_TOKEN)
    output: list[RecognizerResult] = []
    for item in sorted(accepted, key=lambda r: (r.start, r.end, r.entity_type)):
        if output and item.start < output[-1].end:
            previous = output.pop()
            # Stable label choice affects presentation, never coverage.
            kind = min(
                [previous, item], key=lambda r: (-r.score, r.entity_type)
            ).entity_type
            output.append(
                RecognizerResult(
                    kind,
                    previous.start,
                    max(previous.end, item.end),
                    max(previous.score, item.score),
                )
            )
        else:
            output.append(item)
    return output
