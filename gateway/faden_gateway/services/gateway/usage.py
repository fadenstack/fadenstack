"""Token usage: what a provider reports, what the gateway estimates, and what a request reserves.

Counted as the OpenTelemetry GenAI conventions count: the input includes what was read from and written to
the provider's prompt cache, the output includes the model's reasoning. Providers say so in OpenAI's shape
(``prompt_tokens_details.cached_tokens``, ``completion_tokens_details.reasoning_tokens``; vLLM and LiteLLM
too), Anthropic's through LiteLLM adds ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: What a chat request reserves for its answer against the token caps when it sets no ``max_tokens``, as
#: Azure OpenAI and LiteLLM do; the real usage replaces it once the answer is in (ratelimit.py).
DEFAULT_OUTPUT_RESERVATION = 1024


@dataclass(slots=True, frozen=True)
class Usage:
    """Normalized usage counters."""

    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    #: Of the input, read from the provider's prompt cache.
    cached_tokens: int | None = None
    #: Of the input, written to the provider's prompt cache.
    cache_write_tokens: int | None = None
    #: Of the output, the model's reasoning.
    reasoning_tokens: int | None = None

    @property
    def reported(self) -> bool:
        """Whether there are token counts at all."""
        return self.prompt_tokens is not None or self.completion_tokens is not None

    @property
    def tokens(self) -> int:
        """Input and output: what the token caps count."""
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


NO_USAGE = Usage(prompt_tokens=None, completion_tokens=None, total_tokens=None)


def usage_from_payload(payload: dict[str, Any]) -> Usage:
    """Extract token usage from standard OpenAI-compatible payloads."""
    raw = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return NO_USAGE
    prompt_details = _details(raw, "prompt_tokens_details")
    completion_details = _details(raw, "completion_tokens_details")
    return Usage(
        prompt_tokens=_as_int(raw.get("prompt_tokens")),
        completion_tokens=_as_int(raw.get("completion_tokens")),
        total_tokens=_as_int(raw.get("total_tokens")),
        cached_tokens=_first(prompt_details.get("cached_tokens"), raw.get("cache_read_input_tokens")),
        cache_write_tokens=_first(
            raw.get("cache_creation_input_tokens"), prompt_details.get("cache_creation_tokens"),
        ),
        reasoning_tokens=_first(completion_details.get("reasoning_tokens")),
    )


def estimated_usage(input_obj: Any, answer: str | None) -> Usage:
    """Usage the gateway estimates for an answer that came with none: four characters to a token."""
    prompt = estimate_input_tokens(input_obj)
    completion = max(len(answer) // 4, 1) if answer else 0
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)


def add_usage(total: dict[str, Any], usage: Any) -> None:
    """Add one answer's ``usage`` object to *total*: its counts, and those in its ``*_details`` objects.

    For an answer made of several (the rounds of a tool loop, the answers of a split request): each was
    processed, so each counts.
    """
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        if _is_count(value):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict):
            details = total.setdefault(key, {})
            if isinstance(details, dict):
                for name, count in value.items():
                    if _is_count(count):
                        details[name] = details.get(name, 0) + count


def estimate_input_tokens(input_obj: Any) -> int:
    """Cheap heuristic used for MVP TPM checks before upstream response is known."""
    if input_obj is None:
        return 1
    if isinstance(input_obj, str):
        return max(len(input_obj) // 4, 1)
    if isinstance(input_obj, list):
        return max(sum(estimate_input_tokens(chunk) for chunk in input_obj), 1)
    if isinstance(input_obj, dict):
        return max(sum(estimate_input_tokens(v) for v in input_obj.values()), 1)
    return 1


def reserved_tokens(payload: dict[str, Any], endpoint: str) -> int:
    """What a request reserves against the token caps: its input estimate, and for a chat its answer.

    The answer's share is ``max_completion_tokens`` or ``max_tokens`` (times ``n``), else
    ``DEFAULT_OUTPUT_RESERVATION``: admission on an estimate, reconciled with the real usage afterwards.
    """
    tokens = estimate_input_tokens(payload.get("input") or payload.get("messages"))
    if endpoint != "/v1/chat/completions":
        return tokens
    asked = _as_int(payload.get("max_completion_tokens")) or _as_int(payload.get("max_tokens"))
    answer = asked if asked is not None and asked > 0 else DEFAULT_OUTPUT_RESERVATION
    choices = _as_int(payload.get("n"))
    return tokens + answer * (choices if choices is not None and choices > 1 else 1)


def _details(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> int | None:
    """The first of *values* that is a count."""
    for value in values:
        if _is_count(value):
            return int(value)
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
