"""Watermarked answers to the same request differ, as answers without the watermark do.

vLLM's text watermark (Gumbel) picks each token as the argmax of the logits plus noise keyed by the
installation's key and the tokens before it, and by nothing of the request's own: the same request gets
the same answer, ``n`` returns copies and ``seed`` changes nothing (measured on the release server,
fadenstack-dev ``provenance-lab/experiment_repeats.py``). Each answer alone is as varied as without the
watermark; answers to the same request are no longer independent of each other, which breaks "try
again", best-of-n and "give me three suggestions".

An invisible variation at the end of the request's last message changes the model's logits a little; its
choice flips somewhere, and from there on every context differs (``experiment_variety.py``: 3 different
answers of 3, the watermark found in each). The detector reads only the answer, so the watermark is
untouched; only the last message changes, so vLLM's prefix cache holds up to it.

- :func:`vary` adds the variation once per request, to what goes to the model (``req.egress_payload``):
  the gateway's own tool rounds send the same start again, and keep their cache. A client's ``seed`` picks
  the variation, so the same seed gives the same answer again.
- :func:`split` says how many answers a whole answer with ``n`` above 1 is asked for one by one, each with
  its own variation (:func:`merged` joins them); a streamed one is refused (``StreamedChoicesError``):
  its choices would share one stream and one receipt.

Only for a route whose runtime watermarks the answer and that is not asked to decode greedily
(``text_receipts.watermarks``): any other answers as it always did.
"""

from __future__ import annotations

import random
import secrets
from typing import Any

from faden_gateway.services.gateway import text_receipts

#: Word joiner and zero-width space: invisible, never in what the model said back (measured), and not the
#: Unicode marker's own characters (U+FEFF and the variation selectors).
_INVISIBLE = ("\u2060", "\u200b")
#: How many of them: 64 variations, one of which repeats a request's by chance once in 64.
_LENGTH = 6
#: The roles whose message the variation may end: the client's turn, or a tool's answer to the model.
_ROLES = frozenset({"user", "tool"})
#: The most answers a request with ``n`` is split into.
MAX_CHOICES = 16


class StreamedChoicesError(ValueError):
    """``n`` above 1 on a streamed answer of a model that watermarks: refused, not answered with copies."""


def _variation(seed: Any, index: int) -> str:
    """The invisible text for choice *index*: from the client's *seed* when it sent one, else at random."""
    if isinstance(seed, int) and not isinstance(seed, bool):
        pick = random.Random(f"{seed}:{index}").choice  # noqa: S311 - a variation, not a secret
    else:
        pick = secrets.choice
    return "".join(pick(_INVISIBLE) for _ in range(_LENGTH))


def _ended(content: Any, variation: str) -> Any:
    """*content* with *variation* at its end; ``None`` where the message holds no text to end."""
    if isinstance(content, str):
        return content + variation
    if isinstance(content, list) and content:
        parts = list(content)
        for at in range(len(parts) - 1, -1, -1):
            part = parts[at]
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts[at] = {**part, "text": part["text"] + variation}
                return parts
        return [*parts, {"type": "text", "text": variation}]
    return None


def applies(payload: dict[str, Any], candidate: Any) -> bool:
    """Whether *candidate*'s answer to *payload* is watermarked, so that the same request would repeat."""
    return text_receipts.watermarks(candidate, payload)


def vary(payload: dict[str, Any], candidate: Any, *, index: int = 0) -> dict[str, Any]:
    """*payload* with the invisible variation for choice *index* at the end of its last message.

    As it is where the route does not watermark, and where the last message is no client's turn or tool
    answer with text to end (an assistant's message the model is to continue, say).
    """
    if not applies(payload, candidate):
        return payload
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return payload
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") not in _ROLES:
        return payload
    content = _ended(last.get("content"), _variation(payload.get("seed"), index))
    if content is None:
        return payload
    return {**payload, "messages": [*messages[:-1], {**last, "content": content}]}


def _choices(payload: dict[str, Any]) -> int:
    count = payload.get("n")
    return count if isinstance(count, int) and not isinstance(count, bool) else 1


def split(payload: dict[str, Any], candidate: Any, *, stream: bool) -> int | None:
    """How many answers to ask for one by one instead of ``n`` at once; ``None``: ask as the client did.

    Raises :class:`StreamedChoicesError` for a streamed request, and ``ValueError`` for more than
    :data:`MAX_CHOICES`.
    """
    count = _choices(payload)
    if count <= 1 or not applies(payload, candidate):
        return None
    if stream:
        msg = (
            "This model watermarks its answers, so several answers to one request (n above 1) are "
            "given only without streaming. Ask with stream false, or once per answer."
        )
        raise StreamedChoicesError(msg)
    if count > MAX_CHOICES:
        msg = f"This model gives at most {MAX_CHOICES} answers to one request (n)."
        raise ValueError(msg)
    return count


def one_of(payload: dict[str, Any], candidate: Any, index: int) -> dict[str, Any]:
    """The request for answer *index* of a split one: one answer, with its own variation."""
    return vary({**payload, "n": 1}, candidate, index=index)


def merged(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """The answers of a split request as one: their choices in order, their usage added up.

    Each answer's prompt is counted too, as each was processed (most of it from the prefix cache).
    """
    first = payloads[0]
    choices = []
    for index, payload in enumerate(payloads):
        choice = (payload.get("choices") or [{}])[0]
        choices.append({**choice, "index": index})
    usage: dict[str, int] = {}
    for payload in payloads:
        for key, value in (payload.get("usage") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = usage.get(key, 0) + value
    out = {**first, "choices": choices}
    if usage:
        out["usage"] = {**(first.get("usage") or {}), **usage}
    return out
