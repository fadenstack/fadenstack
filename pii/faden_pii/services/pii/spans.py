"""Resolve accepted sensitive coverage independently of model confidence."""

import math
import re
from numbers import Real

from presidio_analyzer import RecognizerResult

LOCAL_TOKEN = re.compile(r"\[\[fs:[A-Z][A-Z0-9_]*_\d+\]\]")


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
    protected = [m.span() for m in LOCAL_TOKEN.finditer(text)]
    accepted = []
    for result in results:
        pieces = [(result.start, result.end)]
        for left, right in protected:
            pieces = [
                (a, b)
                for start, end in pieces
                for a, b in [(start, min(end, left)), (max(start, right), end)]
                if a < b
            ]
        accepted.extend(
            RecognizerResult(result.entity_type, a, b, result.score) for a, b in pieces
        )
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
