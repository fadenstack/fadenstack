"""Every model answer with text gets a signed receipt, with the provenance module on.

Once an answer is complete -- after its last token, never before its first -- the gateway asks the
provenance engine for a receipt (``ProvenanceClient.receipt``) with the hash of the answer's text as the
client got it (``content_hash.text_sha256``: the engine never sees the text), the route's model, the
agent, and whether the text carries a watermark (``watermark``, the table in the engine's docs/api.md).

- A streamed answer gets one more event before ``[DONE]``: ``faden.provenance``, with the signed
  receipt, for a client that asked for the gateway's events (``X-Faden-Events``). The receipt is asked
  for either way, so the engine keeps it; a client that did not ask does not wait for it.
- A whole answer gets the receipt's id in ``X-Faden-Provenance``, before the receipts of the pictures
  it carries; a client that asked for events gets the event in the answer's ``faden`` object too.
- An answer without text (tool calls only) gets none.

The engine failing never holds an answer back or fails it: the event says the answer is unsigned
(``server-unavailable``), a whole answer leaves without the header, and the log gets one line -- never
the text.
"""

from __future__ import annotations

import logging
from typing import Any

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import content_hash
from faden_gateway.services.gateway.provenance_client import (
    ProvenanceClient,
    ProvenanceRequestError,
    SignedReceipt,
)
from faden_gateway.services.gateway.usage import estimate_input_tokens

logger = logging.getLogger(__name__)

#: The Faden event that carries an answer's receipt.
EVENT = "faden.provenance"

#: Under this many completion tokens a text carries no watermark (the short-text exemption).
SHORT_TEXT_TOKENS = 200

#: What a stream's event says when the engine gave no receipt.
UNSIGNED: dict[str, Any] = {"receipt": None, "status": "unsigned", "reason": "server-unavailable"}

#: The remote providers known to watermark their models' text, by the name their mark goes under.
_MARKING_PROVIDERS = {ProviderType.REMOTE_ANTHROPIC: "anthropic"}
_MARKING_LITELLM_PROVIDERS = {"anthropic": "anthropic"}


def answer_text(payload: Any) -> str:
    """A whole answer's first choice's text, as the client gets it: its content, or its text parts in turn."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = (part for part in content if isinstance(part, dict) and part.get("type") == "text")
        return "".join(str(part.get("text") or "") for part in parts)
    return ""


def has_text(text: str) -> bool:
    """Whether *text* holds anything once normalised: an answer of tool calls only, or of spaces, does not."""
    return bool(content_hash.normalize_text(text))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _greedy(params: dict[str, Any]) -> bool:
    """Whether the model was asked to decode greedily: temperature 0, beam search, or top-k 1."""
    nested = params.get("extra_body")
    for source in (params, nested if isinstance(nested, dict) else {}):
        if _number(source.get("temperature")) == 0 or source.get("use_beam_search") is True:
            return True
        if _number(source.get("top_k")) == 1:
            return True
    return False


def _switched_off(params: dict[str, Any]) -> bool:
    """Whether the route's own parameters switch vLLM's watermark off (``watermarking: false``).

    A client's never reach the model (the adapter passes OpenAI's parameters only); an administrator's
    route parameters do, so the receipt says the text carries none.
    """
    nested = params.get("extra_body")
    return any(
        source.get("watermarking") is False for source in (params, nested if isinstance(nested, dict) else {})
    )


def _remote(candidate: Any) -> bool:
    provider = getattr(candidate, "provider_type", None)
    return str(getattr(provider, "value", provider) or "").startswith("remote_")


def _marking_vendor(candidate: Any) -> str | None:
    """The remote provider that marks its models' text itself, when the route goes to one."""
    vendor = _MARKING_PROVIDERS.get(getattr(candidate, "provider_type", None))
    if vendor is None:
        litellm_provider = str(getattr(candidate, "litellm_provider", None) or "").lower()
        vendor = _MARKING_LITELLM_PROVIDERS.get(litellm_provider)
    return vendor


def _runtime_mark(candidate: Any) -> dict[str, Any] | None:
    """How the route's runtime watermarks the text (``node_metadata["watermark"]``); ``None``: not at all.

    The backend publishes ``{"algorithm", "key_version", "context_width", "tokenizer"}`` for a deployment
    it started with the watermark: what the engine needs to check a text for it later.
    """
    said = (getattr(candidate, "node_metadata", None) or {}).get("watermark")
    if not said:
        return None
    if isinstance(said, str):
        return {"algorithm": said}
    if not isinstance(said, dict):
        return {}
    mark: dict[str, Any] = {}
    if isinstance(said.get("algorithm"), str):
        mark["algorithm"] = said["algorithm"]
    for field in ("key_version", "context_width"):
        value = said.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            mark[field] = value
    tokenizer = said.get("tokenizer")
    if isinstance(tokenizer, str) and 0 < len(tokenizer) <= 256:
        mark["tokenizer"] = tokenizer
    return mark


def watermark(
    candidate: Any, sent: dict[str, Any], *, completion_tokens: int | None, text: str,
) -> dict[str, Any]:
    """The receipt's ``watermark`` (``WatermarkStatus``) for an answer of *candidate*'s to the request *sent*.

    In this order: under ``SHORT_TEXT_TOKENS`` completion tokens (counted from
    the text when the model gave no usage), ``short-text``; asked to decode
    greedily (by the request or the route's own parameters), ``no-watermark-greedy``;
    a local route, ``model`` when its runtime says it watermarks, else
    ``runtime-without-watermark``; a remote provider that marks its text
    (Anthropic), ``upstream`` with the provider; any other, ``no-watermark-remote-model``.
    """
    tokens = completion_tokens if completion_tokens is not None else estimate_input_tokens(text)
    if tokens < SHORT_TEXT_TOKENS:
        return {"applied": "none", "reason": "short-text"}
    if _greedy({**sent, **(getattr(candidate, "extra_params", None) or {})}):
        return {"applied": "none", "reason": "no-watermark-greedy"}
    if not _remote(candidate):
        mark = _runtime_mark(candidate)
        if mark is None or _switched_off(getattr(candidate, "extra_params", None) or {}):
            return {"applied": "none", "reason": "runtime-without-watermark"}
        return {"applied": "model", **mark}
    vendor = _marking_vendor(candidate)
    if vendor is not None:
        return {"applied": "upstream", "vendor": vendor}
    return {"applied": "none", "reason": "no-watermark-remote-model"}


async def sign(
    client: ProvenanceClient,
    text: str,
    *,
    candidate: Any,
    sent: dict[str, Any],
    completion_tokens: int | None,
    model: str,
    agent: str | None,
    request_id: str,
) -> SignedReceipt | None:
    """The engine's signed receipt for the answer *text*; ``None``, with one log line, when there is none.

    *candidate* is the route that answered the request *sent*, with
    *completion_tokens*: what the receipt's ``watermark`` says comes from them.
    Never raises: a receipt the engine cannot sign never fails the answer.
    """
    try:
        status = watermark(candidate, sent, completion_tokens=completion_tokens, text=text)
        return await client.receipt(
            content_sha256=content_hash.text_sha256(text), model=model, agent=agent, watermark=status,
        )
    except ProvenanceRequestError as exc:
        logger.warning("Request %s: the answer's receipt was not signed (%s): %s", request_id, exc.code, exc)
    except Exception as exc:  # the answer matters more than its receipt
        logger.warning("Request %s: the answer's receipt was not signed (%s)", request_id, type(exc).__name__)
        logger.debug("The receipt's failure", exc_info=True)
    return None


def event(signed: SignedReceipt | None) -> dict[str, Any]:
    """The ``faden.provenance`` event: the signed receipt, or that the answer is unsigned."""
    value = signed.to_dict() if signed is not None else dict(UNSIGNED)
    return {"type": "CUSTOM", "name": EVENT, "value": value}
