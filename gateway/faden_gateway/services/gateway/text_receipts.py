"""Every model answer with text gets a signed receipt, with the provenance module on.

Once an answer is complete -- after its last token, never before its first -- the gateway asks the
provenance engine for a receipt (``ProvenanceClient.receipt``) with the hash of the answer's text as the
client got it (``content_hash.text_sha256``: the engine never sees the text), the route's model, the
agent, and whether the text carries a watermark (``watermark``, the table in the engine's docs/api.md).

- A streamed answer gets one more event before ``[DONE]``: ``faden.provenance``, with the signed
  receipt, for a client that asked for the gateway's events (``X-Faden-Events``). The receipt is asked
  for either way, so the engine keeps it; a client that did not ask does not wait for it.
- A whole answer gets the receipt's id in ``X-Faden-Provenance``, before the receipts of the pictures
  it carries; a client that asked for events gets the event in the answer's ``faden`` object too. With
  several choices (``n``), each gets its own receipt, in their order.
- The receipt covers the turn's whole output: its text, then each tool call's arguments as the model wrote
  them (sdk/14 §1): the text an agent writes into a document arrives as tool arguments. A turn with neither
  gets none.

The engine failing never holds an answer back or fails it: the event says the answer is unsigned
(``server-unavailable``), a whole answer leaves without the header, and the log gets one line -- never
the text.
"""

from __future__ import annotations

import logging
from typing import Any

from faden_gateway.services.gateway import content_hash, text_fingerprint
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

def _choices(payload: Any) -> list[dict[str, Any]]:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    return [c for c in choices if isinstance(c, dict)] if isinstance(choices, list) else []


def _choice_text(choice: dict[str, Any]) -> str:
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = (part for part in content if isinstance(part, dict) and part.get("type") == "text")
        return "".join(str(part.get("text") or "") for part in parts)
    return ""


def _choice_output(choice: dict[str, Any]) -> str:
    message = choice.get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return turn_output(_choice_text(choice), calls if isinstance(calls, list) else None)


def answer_text(payload: Any) -> str:
    """A whole answer's first choice's text, as the client gets it: its content, or its text parts in turn."""
    choices = _choices(payload)
    return _choice_text(choices[0]) if choices else ""


def turn_output(text: str, tool_calls: list[dict[str, Any]] | None) -> str:
    """What a turn's receipt covers: its text, then each tool call's arguments as the model wrote them."""
    parts = [text] if text else []
    for call in tool_calls or []:
        arguments = (call.get("function") or {}).get("arguments") if isinstance(call, dict) else None
        if isinstance(arguments, str) and arguments:
            parts.append(arguments)
    return "\n".join(parts)


def answer_output(payload: Any) -> str:
    """A whole answer's :func:`turn_output`: its first choice's text and tool calls."""
    choices = _choices(payload)
    return _choice_output(choices[0]) if choices else ""


def answer_outputs(payload: Any) -> list[str]:
    """Each choice's :func:`turn_output`, in order: one per answer a request with ``n`` got."""
    return [_choice_output(choice) for choice in _choices(payload)]


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


def remote(candidate: Any) -> bool:
    """Whether *candidate* is a remote provider's route (``remote_*``)."""
    provider = getattr(candidate, "provider_type", None)
    return str(getattr(provider, "value", provider) or "").startswith("remote_")


def upstream_mark(candidate: Any) -> dict[str, Any] | None:
    """The evidence an administrator gave that *candidate*'s remote provider marks its text; ``None``: none.

    No provider counts as marking by its name: Anthropic marks per model, OpenAI's mark is an account
    setting, and no answer of either says whether it was marked. The backend publishes the evidence per
    route, in ``node_metadata["text_marking"]["upstream"]`` (text_marking.py).
    """
    policy = (getattr(candidate, "node_metadata", None) or {}).get("text_marking")
    said = policy.get("upstream") if isinstance(policy, dict) else None
    if not remote(candidate) or not isinstance(said, dict) or not said.get("evidence_url"):
        return None
    return said


def _vendor(candidate: Any, evidence: dict[str, Any]) -> str:
    named = evidence.get("vendor")
    if isinstance(named, str) and named:
        return named
    provider = getattr(candidate, "provider_type", None)
    return str(getattr(provider, "value", provider) or "remote").removeprefix("remote_")


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


def watermarks(candidate: Any, sent: dict[str, Any]) -> bool:
    """Whether *candidate*'s runtime watermarks its answer to the request *sent*, whatever its length.

    A local route whose runtime says it watermarks, not switched off by the route's own parameters, and
    not asked to decode greedily (vLLM never watermarks that).
    """
    params = getattr(candidate, "extra_params", None) or {}
    return runtime_marks(candidate) and not _greedy({**sent, **params})


def runtime_marks(candidate: Any) -> bool:
    """Whether *candidate* is a local route whose runtime writes the watermark, not switched off."""
    params = getattr(candidate, "extra_params", None) or {}
    return not remote(candidate) and _runtime_mark(candidate) is not None and not _switched_off(params)


def watermark(
    candidate: Any,
    sent: dict[str, Any],
    *,
    completion_tokens: int | None,
    text: str,
    purpose: str | None = None,
) -> dict[str, Any]:
    """The receipt's ``watermark`` (``WatermarkStatus``) for an answer of *candidate*'s to the request *sent*.

    In this order: under ``SHORT_TEXT_TOKENS`` completion tokens (counted from
    the text when the model gave no usage), ``short-text``; asked to decode
    greedily (by the request or the route's own parameters), ``no-watermark-greedy``;
    a local route, ``model`` when its runtime says it watermarks, else
    ``runtime-without-watermark``; a remote provider with evidence that it marks
    its text (``upstream_mark``), ``upstream`` with the provider; any other,
    ``no-watermark-remote-model``. Work the request declared exempt (*purpose*,
    text_marking.py) on a route that does not mark is ``exempt-edit``.
    """
    tokens = completion_tokens if completion_tokens is not None else estimate_input_tokens(text)
    if tokens < SHORT_TEXT_TOKENS:
        return {"applied": "none", "reason": "short-text"}
    if _greedy({**sent, **(getattr(candidate, "extra_params", None) or {})}):
        return {"applied": "none", "reason": "no-watermark-greedy"}
    if runtime_marks(candidate):
        return {"applied": "model", **(_runtime_mark(candidate) or {})}
    evidence = upstream_mark(candidate)
    if evidence is not None:
        return {"applied": "upstream", "vendor": _vendor(candidate, evidence)}
    if purpose is not None:
        reason = "exempt-edit"
    else:
        reason = "no-watermark-remote-model" if remote(candidate) else "runtime-without-watermark"
    return {"applied": "none", "reason": reason}


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
    purpose: str | None = None,
) -> SignedReceipt | None:
    """The engine's signed receipt for the answer *text*; ``None``, with one log line, when there is none.

    *candidate* is the route that answered the request *sent*, with
    *completion_tokens*: what the receipt's ``watermark`` says comes from them.
    Never raises: a receipt the engine cannot sign never fails the answer.
    """
    try:
        status = watermark(candidate, sent, completion_tokens=completion_tokens, text=text, purpose=purpose)
        return await client.receipt(
            content_sha256=content_hash.text_sha256(text), model=model, agent=agent, watermark=status,
            fingerprint=text_fingerprint.fingerprint(text),
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
