"""LiteLLM adapter — unified LLM completion / embedding interface.

Wraps ``litellm.acompletion`` and ``litellm.aembedding`` to provide a
provider-agnostic calling layer.  The gateway service delegates all
upstream calls through this adapter instead of the raw HTTP proxy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from typing import Any

import httpx
import litellm

from faden_gateway.db.crypto import decrypt_value
from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import upstream_tls
from faden_gateway.services.gateway.ssl_helpers import build_ssl_kwargs
from faden_gateway.settings import settings

logger = logging.getLogger(__name__)

#: The OpenAI chat parameters a request may carry on to the model.
_OPENAI_PARAMS = (
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "user",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "n",
)
#: vLLM's field for a chat template's own switches (Qwen's ``enable_thinking``, ...).
_TEMPLATE_KWARGS = "chat_template_kwargs"
#: The ``reasoning_effort`` values vLLM accepts: it validates the field.
_VLLM_EFFORTS = frozenset({"low", "medium", "high"})
#: What the body of a direct streamed chat is made of.
_DIRECT_BODY_KEYS = frozenset(
    {"messages", "stream", "stream_options", "reasoning_effort", _TEMPLATE_KWARGS, *_OPENAI_PARAMS},
)
_DONE = b"data: [DONE]\n\n"


# Models whose chat template refused the gateway's system messages: theirs go
# as one, first, from then on (see _late_system_refused).
_SYSTEM_FIRST: set[str] = set()
_TEMPLATE_REFUSAL = re.compile(r"template|roles? must alternate|system (role|message)", re.IGNORECASE)

#: How much a model may reason, least first: the order a refused level steps along.
_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: Where a refused level goes when the refusal names none the model takes.
_EFFORT_STEP = {"none": "low", "minimal": "low", "xhigh": "high", "max": "high"}
_EFFORT_REFUSAL = re.compile(r"reasoning|thinking|effort", re.IGNORECASE)
#: A quoted word, as refusals list the levels a model takes: 'low', "low", or \"low\" inside JSON.
_QUOTED_WORD = re.compile(r"""['"`]([a-z]+)\\?['"`]""")
# Levels a model refused, per model, and what it is asked for instead ("": no
# level at all, its default), from then on (see _effort_refused).
_EFFORT_INSTEAD: dict[tuple[str, str], str] = {}
#: How many times a refused request goes again, at most: with its system
#: messages up front, at a nearer level of reasoning, without one.
_RETRIES = 3


def _reasoning(payload: dict[str, Any], provider_type: ProviderType) -> dict[str, Any]:
    """How much the model reasons, in the form its server understands.

    ``reasoning_effort`` is OpenAI's: LiteLLM maps it per provider and drops it
    for a model without one (``drop_params``). vLLM validates it (low, medium,
    high), and a template with an on/off switch (Qwen's ``enable_thinking``)
    ignores it; so for vLLM "none" and "minimal" turn the switch off instead,
    "xhigh" asks for "high", and the client's own ``chat_template_kwargs`` go
    along. Until 2026-10-03 neither field reached any model: a client's "no
    thinking" was dropped here. A level the model refuses goes again at the
    nearest one it takes, or not at all (see _effort_refused).
    """
    effort = payload.get("reasoning_effort")
    effort = effort.lower() if isinstance(effort, str) and effort else None
    if provider_type != ProviderType.VLLM:
        return {"reasoning_effort": effort} if effort else {}
    given = payload.get(_TEMPLATE_KWARGS)
    template: dict[str, Any] = dict(given) if isinstance(given, dict) else {}
    out: dict[str, Any] = {}
    if effort in ("none", "minimal"):
        template.setdefault("enable_thinking", False)
    elif effort == "xhigh":
        out["reasoning_effort"] = "high"
    elif effort in _VLLM_EFFORTS:
        out["reasoning_effort"] = effort
    if template:
        out[_TEMPLATE_KWARGS] = template
    return out


def _for_litellm(kwargs: dict[str, Any], provider_type: ProviderType) -> dict[str, Any]:
    """The fields of vLLM's own go to LiteLLM as ``extra_body``: it would drop them as unknown parameters."""
    if provider_type != ProviderType.VLLM:
        return kwargs
    own = {key: kwargs.pop(key) for key in ("reasoning_effort", _TEMPLATE_KWARGS) if key in kwargs}
    if own:
        kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), **own}
    return kwargs


def _text_of(content: Any) -> str:
    """A message's text, whether it is a string or OpenAI's list of parts."""
    if isinstance(content, list):
        parts = (part for part in content if isinstance(part, dict) and part.get("type") == "text")
        return "\n".join(str(part.get("text") or "") for part in parts)
    return "" if content is None else str(content)


def _system_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """*messages* with all their system messages as one, first.

    That is the one form every chat template takes: Qwen 3.5 and later
    refuse any system message but the first -- two in a row as well as one
    after the history -- and others refuse one after the conversation has
    begun. The system texts keep their order, a blank line apart, and so do
    the other messages. None when *messages* already have that form.
    """
    system = [m for m in messages if m.get("role") == "system"]
    if not system or (len(system) == 1 and messages[0] is system[0]):
        return None
    texts = (_text_of(m.get("content")).strip() for m in system)
    merged = {"role": "system", "content": "\n\n".join(text for text in texts if text)}
    return [merged, *(m for m in messages if m.get("role") != "system")]


def _late_system_refused(
    status: int, detail: str, messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """The messages to try again with, when a template refused the system messages.

    The gateway sends several: the date, the PII note, memory, a turn's
    skills after the history, so the prefix vLLM caches stays the same
    (``session_skills_position``). Llama 3's template takes them anywhere;
    one that does not answers 400 with the template's error. That request
    then goes again with its system messages as one, first.
    """
    if status != 400 or not _TEMPLATE_REFUSAL.search(detail):
        return None
    return _system_first(messages)


def _remember_system_first(model: str) -> None:
    """Send *model*'s system messages as one, first, from now on; say so once."""
    if model not in _SYSTEM_FIRST:
        _SYSTEM_FIRST.add(model)
        logger.warning(
            "%s's chat template refuses more than one system message, or one after the start; "
            "its system messages go as one, before the history, from now on",
            model,
        )


def _effort_of(request: dict[str, Any]) -> str | None:
    """The level of reasoning *request* asks for: its own field, or vLLM's in ``extra_body``."""
    extra = request.get("extra_body")
    value = extra.get("reasoning_effort") if isinstance(extra, dict) else None
    if value is None:
        value = request.get("reasoning_effort")
    return value if isinstance(value, str) and value else None


def _with_effort(request: dict[str, Any], effort: str | None) -> dict[str, Any]:
    """*request* asking for *effort* instead, or for no level at all (None): the model's default."""
    extra = request.get("extra_body")
    if isinstance(extra, dict) and "reasoning_effort" in extra:
        # vLLM's through LiteLLM: the field sits in extra_body, as at the top of a direct body.
        return {**request, "extra_body": _with_effort(extra, effort)}
    changed = {key: value for key, value in request.items() if key != "reasoning_effort"}
    if effort:
        changed["reasoning_effort"] = effort
    return changed


def _effort_instead(model: str, refused: str, detail: str) -> str | None:
    """The level to ask *model* for after it refused *refused*; None for no level at all.

    The nearest of the levels the refusal names ("Supported values are:
    'low', 'medium', and 'high'"), on a tie the one nearer "medium"; else
    one step toward the middle: "none" and "minimal" to "low", "xhigh" and
    "max" to "high". A level the model refused before is not asked for
    again. A "low", "medium" or "high" refused without a list has nowhere
    to go: the model has no levels, or not these.
    """
    tried = {level for name, level in _EFFORT_INSTEAD if name == model} | {refused}
    if refused in _EFFORTS:
        named = {word for word in _QUOTED_WORD.findall(detail.lower()) if word in _EFFORTS} - tried
        if named:
            at, middle = _EFFORTS.index(refused), _EFFORTS.index("medium")

            def distance(level: str) -> tuple[int, int]:
                return abs(_EFFORTS.index(level) - at), abs(_EFFORTS.index(level) - middle)

            return min(named, key=distance)
    step = _EFFORT_STEP.get(refused)
    return step if step not in tried else None


def _remember_effort(model: str, refused: str, instead: str | None) -> None:
    """Ask *model* for *instead* rather than *refused* from now on; say so once."""
    if _EFFORT_INSTEAD.get((model, refused)) != (instead or ""):
        _EFFORT_INSTEAD[(model, refused)] = instead or ""
        logger.warning(
            "%s refuses reasoning_effort %r: it is asked for %s from now on",
            model, refused, repr(instead) if instead else "no level, its own default,",
        )


def _remembered_effort(request: dict[str, Any], model: str) -> dict[str, Any]:
    """*request* at the level *model* takes, for one it refused before."""
    asked = effort = _effort_of(request)
    seen: set[str] = set()
    while effort is not None and (model, effort) in _EFFORT_INSTEAD and effort not in seen:
        seen.add(effort)
        effort = _EFFORT_INSTEAD[(model, effort)] or None
    return request if effort == asked else _with_effort(request, effort)


def _effort_refused(status: int, detail: str, request: dict[str, Any], model: str) -> dict[str, Any] | None:
    """The request to try again with, when the model refused the level of reasoning it was asked for.

    ``reasoning_effort`` is the client's preference, not a condition, so the
    client never sees such a refusal. A model with levels of its own refuses
    one it lacks -- OpenAI's reasoning models list the ones they take, a
    model that always thinks refuses "none", LiteLLM refuses "xhigh" for a
    Claude without it -- and a model without levels may refuse the field.
    The request goes again at the nearest level the model takes (see
    _effort_instead), or without one, and the model gets that from then on.
    """
    refused = _effort_of(request)
    if refused is None or status not in (400, 422) or not _EFFORT_REFUSAL.search(detail):
        return None
    instead = _effort_instead(model, refused, detail)
    _remember_effort(model, refused, instead)
    return _with_effort(request, instead)


def _try_again(status: int, detail: str, request: dict[str, Any], model: str) -> dict[str, Any] | None:
    """The request to send again after a refusal one change may cure; None after any other."""
    moved = _late_system_refused(status, detail, request.get("messages") or [])
    if moved is not None:
        _remember_system_first(model)
        return {**request, "messages": moved}
    return _effort_refused(status, detail, request, model)


async def _whole_events(response: httpx.Response) -> AsyncIterator[str]:
    """The engine's server-sent events, each whole, however the network cut them.

    A read can split one event or carry several; the steps after the stream
    -- PII restore, usage, the saved reply -- read whole events.
    """
    lines: list[str] = []
    async for line in response.aiter_lines():
        if line:
            if not line.startswith(":"):  # a comment keeps the connection alive
                lines.append(line)
            continue
        if lines:
            yield "\n".join(lines)
            lines = []
    if lines:
        yield "\n".join(lines)


def _sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


#: What a streamed delta carries: the OpenAI format's keys, and a reasoning model's thinking.
_DELTA_KEYS = frozenset({"role", "content", "tool_calls", "function_call", "refusal"})


def reasoning_of(delta: dict[str, Any]) -> str | None:
    """A delta's reasoning text, under either name providers give it."""
    text = delta.get("reasoning_content") or delta.get("reasoning")
    return text if isinstance(text, str) and text else None


def openai_chunk(data: dict[str, Any]) -> dict[str, Any]:
    """A LiteLLM stream chunk as the OpenAI format has it, with the model's thinking kept.

    LiteLLM puts its own fields in every delta -- ``audio``, ``function_call``,
    ``provider_specific_fields``, mostly null -- and a reasoning model's thinking
    twice: ``reasoning`` (OpenRouter's name) and ``reasoning_content`` (LiteLLM's
    and DeepSeek's). The client gets it once, as ``reasoning_content``, which is
    what vLLM, DeepSeek and the chat clients that show thinking read.
    """
    for choice in data.get("choices") or []:
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            continue
        reasoning = reasoning_of(delta)
        clean = {k: v for k, v in delta.items() if k in _DELTA_KEYS and v is not None}
        if reasoning:
            clean["reasoning_content"] = reasoning
        choice["delta"] = clean
    return data


def _is_done(event: str) -> bool:
    return event.replace(" ", "") == "data:[DONE]"

# ── Provider type → LiteLLM provider prefix mapping ──────────────────
_PROVIDER_PREFIX: dict[str, str] = {
    ProviderType.REMOTE_OPENAI: "openai",
    ProviderType.REMOTE_ANTHROPIC: "anthropic",
    ProviderType.REMOTE_GOOGLE: "gemini",
    ProviderType.REMOTE_BEDROCK: "bedrock",
    ProviderType.REMOTE_AZURE: "azure",
    ProviderType.REMOTE_MISTRAL: "mistral",
    ProviderType.REMOTE_GROQ: "groq",
    ProviderType.REMOTE_DEEPSEEK: "deepseek",
    ProviderType.REMOTE_COHERE: "cohere",
    ProviderType.REMOTE_CUSTOM: "openai",
    # Local inference engines — all speak OpenAI protocol
    ProviderType.VLLM: "openai",
    ProviderType.LLAMACPP: "openai",
    ProviderType.TGI: "openai",
    ProviderType.OLLAMA: "ollama",
}


@dataclass(slots=True, frozen=True)
class CompletionResult:
    """Unified non-streaming completion result."""

    status_code: int
    payload: dict[str, Any]


def _build_litellm_model_name(
    *,
    provider_type: ProviderType,
    litellm_provider: str | None,
    litellm_model: str | None,
    requested_model: str,
) -> str:
    """Build the ``model`` string that LiteLLM expects.

    LiteLLM uses a ``provider/model`` naming convention.  If the user
    has configured an explicit ``litellm_provider`` and ``litellm_model``
    we use those.  Otherwise we derive them from the ``ProviderType``
    and the model alias sent in the request.
    """
    prefix = litellm_provider or _PROVIDER_PREFIX.get(provider_type, "openai")
    model = litellm_model or requested_model

    # Google's API returns model names with a "models/" prefix
    # (e.g. "models/gemini-2.0-flash-lite") — strip it for LiteLLM.
    if model.startswith("models/"):
        model = model[len("models/") :]

    # For local OpenAI-compatible engines (vLLM, TGI, llama.cpp) we
    # always route through LiteLLM's "openai" provider so it uses the
    # custom api_base.  The model name after the prefix is passed
    # verbatim to the engine.
    if provider_type in (
        ProviderType.VLLM,
        ProviderType.LLAMACPP,
        ProviderType.TGI,
    ):
        return f"openai/{model}"

    if provider_type == ProviderType.OLLAMA:
        return f"ollama/{model}"

    # Remote providers — use explicit prefix/model
    return f"{prefix}/{model}"


def _resolve_api_key(encrypted_key: str | None) -> str | None:
    """Decrypt the Fernet-encrypted API key stored in the DB."""
    if not encrypted_key:
        return None
    try:
        return decrypt_value(encrypted_key, purpose="provider-api-key")
    except Exception:
        logger.warning("Failed to decrypt provider API key; sending without auth")
        return None


def _upstream_kwargs(
    *,
    base_url: str | None,
    api_key: str | None,
    provider_type: ProviderType,
) -> dict[str, Any]:
    """Build the LiteLLM kwargs that select the upstream endpoint.

    Two concerns live here so every calling method (chat completion,
    streaming completion, embeddings) stays consistent:

    * **``api_base``** — the engine and proxied OpenAI-compatible
      servers whose Chat Completions / Embeddings API lives under a
      ``/v1`` prefix.  The DB stores a normalised base URL with any
      trailing ``/v1`` stripped (see the backend ``gateway_sync``
      normaliser, which assumed the legacy HTTP proxy would re-add it),
      so we restore it here for every type whose upstream expects the
      ``/v1`` path segment.  LiteLLM (via the OpenAI SDK) appends the
      endpoint path directly to ``api_base``, so we must not add a second
      ``/v1`` for cloud providers reached at their default base URL.
    * **``api_key``** — any endpoint the operator pointed us at
      explicitly (self-hosted OpenAI-compatible server, local inference
      engine, proxy, …) with no key configured for the provider.  The
      OpenAI SDK refuses to initialise without an api_key value even
      though the endpoint needs none, so we send the conventional
      ``"EMPTY"`` placeholder.  Cloud providers reached at their default
      base URL (no ``api_base`` set) never take this branch: they require
      a real key and will fail with a clear 401 instead.
    """
    kwargs: dict[str, Any] = {}
    if base_url and not base_url.startswith("litellm://"):
        effective_base = base_url.rstrip("/")
        # Local engines (vLLM/TGI) and proxied OpenAI-compatible servers
        # serve the Chat Completions / Embeddings API under a /v1 prefix;
        # restore the segment the backend normaliser stripped on save.
        # (Full rationale: see the docstring above.)
        # Groq and Mistral are OpenAI-compatible under /v1 as well
        # (api.groq.com/openai/v1, api.mistral.ai/v1): their address, as the
        # console fills it in, lost the segment on save like any other.
        v1_prefixed = (
            ProviderType.VLLM,
            ProviderType.TGI,
            ProviderType.REMOTE_OPENAI,
            ProviderType.REMOTE_GROQ,
            ProviderType.REMOTE_MISTRAL,
        )
        if provider_type in v1_prefixed and not effective_base.endswith("/v1"):
            effective_base += "/v1"
        kwargs["api_base"] = effective_base
    if api_key:
        kwargs["api_key"] = api_key
    elif kwargs.get("api_base"):
        kwargs["api_key"] = "EMPTY"
    return kwargs


#: Provider types LiteLLM reaches through the OpenAI SDK ("openai/..."): a
#: TLS setting of their own goes in as a ready-made ``AsyncOpenAI``.
_OPENAI_SDK_TYPES = frozenset({
    ProviderType.VLLM,
    ProviderType.LLAMACPP,
    ProviderType.TGI,
    ProviderType.REMOTE_OPENAI,
    ProviderType.REMOTE_CUSTOM,
})
_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
_WARNED_NO_CLIENT_CERT: set[str] = set()


class _TlsRefusedError(Exception):
    """A model this gateway cannot reach securely yet; the message says why."""


def _provider_tls(
    *,
    instance_id: uuid.UUID | None,
    ssl_verify_mode: str | None,
    ssl_ca_bundle_pem: str | None,
    ssl_client_cert_pem: str | None,
    ssl_client_key_pem: str | None,
) -> upstream_tls.UpstreamTls | None:
    if instance_id is None:
        return None
    try:
        return upstream_tls.for_provider(
            ssl_verify_mode=ssl_verify_mode,
            ssl_ca_bundle_pem=ssl_ca_bundle_pem,
            ssl_client_cert_pem=ssl_client_cert_pem,
            ssl_client_key_pem=ssl_client_key_pem,
        )
    except upstream_tls.MachineTlsUnavailableError as exc:
        raise _TlsRefusedError(str(exc)) from exc


def _native_kwargs(
    tls: upstream_tls.UpstreamTls | None,
    *,
    provider_type: ProviderType,
    instance_id: uuid.UUID | None,
    ssl_verify_mode: str | None,
    ssl_ca_bundle_pem: str | None,
    ssl_client_cert_pem: str | None,
    ssl_client_key_pem: str | None,
) -> dict[str, Any]:
    """The per-call kwargs for a provider LiteLLM speaks natively, which honour a CA only."""
    if tls is None or provider_type in _OPENAI_SDK_TYPES or instance_id is None:
        return {}
    return build_ssl_kwargs(
        instance_id=instance_id,
        ssl_verify_mode=ssl_verify_mode,
        ssl_ca_bundle_pem=ssl_ca_bundle_pem,
        ssl_client_cert_pem=ssl_client_cert_pem,
        ssl_client_key_pem=ssl_client_key_pem,
    )


def _apply_tls(
    kwargs: dict[str, Any],
    tls: upstream_tls.UpstreamTls | None,
    *,
    provider_type: ProviderType,
    instance_id: uuid.UUID | None,
    native: dict[str, Any],
) -> None:
    """Give LiteLLM a client that carries *tls*, where it takes one.

    The OpenAI-compatible types get an ``AsyncOpenAI`` over a client made with
    the setting's own SSL context -- the only way the CA and the client
    certificate reach the connection (LiteLLM builds that path's client from
    its globals). The providers LiteLLM speaks natively keep the per-call
    ``ssl_verify`` they honour; a client certificate cannot be presented to
    them, and that is said once.
    """
    if tls is None:
        return
    if provider_type in _OPENAI_SDK_TYPES:
        kwargs["client"] = upstream_tls.clients.openai(
            tls, base_url=str(kwargs.get("api_base") or _OPENAI_DEFAULT_BASE), api_key=kwargs.get("api_key"),
        )
        return
    kwargs.update(native)
    if tls.cert_pem and str(provider_type) not in _WARNED_NO_CLIENT_CERT:
        _WARNED_NO_CLIENT_CERT.add(str(provider_type))
        logger.warning(
            "Provider %s (%s) has a client certificate, which LiteLLM cannot present for this "
            "provider type; its CA setting is applied, the certificate is not.",
            instance_id, provider_type,
        )


def _refusal(stream: bool, message: str) -> Any:
    payload = _error_payload("server_error", message)
    if not stream:
        return CompletionResult(status_code=503, payload=payload)

    async def _events() -> AsyncIterator[bytes]:
        yield _sse(payload)
        yield _DONE

    return _events()


class LLMAdapter:
    """Provider-agnostic adapter backed by LiteLLM.

    A streamed chat to a vLLM engine is the exception: given *http_client*, it
    goes straight to the engine (see :meth:`_stream_direct`).
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client

    async def completion(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        payload: dict[str, Any],
        stream: bool = False,
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
        first_token_timeout_sec: float | None = None,
    ) -> CompletionResult | AsyncIterator[Any]:
        """Run a chat completion (streaming or non-streaming).

        Returns ``CompletionResult`` for non-streaming, or an async
        iterator of ``ModelResponse`` chunks for streaming.
        *first_token_timeout_sec* is the model's own wait for a streamed
        reply to start (see :meth:`_stream_direct`).
        """
        model_name = _build_litellm_model_name(
            provider_type=provider_type,
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            requested_model=payload.get("model", ""),
        )
        api_key = _resolve_api_key(api_key_encrypted)

        # Build kwargs for litellm.acompletion
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": payload.get("messages", []),
            "stream": stream,
        }
        # Resolve the upstream endpoint (api_base + api_key) the same
        # way every other Litellm method does (see :func:`_upstream_kwargs`).
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=api_key,
                provider_type=provider_type,
            ),
        )

        # Pass through supported OpenAI params
        for key in _OPENAI_PARAMS:
            if key in payload:
                kwargs[key] = payload[key]
        kwargs.update(_reasoning(payload, provider_type))

        # Merge extra_params (custom headers, api_version, etc.)
        extra_headers: dict[str, Any] = {}
        litellm_only: dict[str, Any] = {}
        if extra_params:
            given_headers = extra_params.pop("extra_headers", None)
            if given_headers and isinstance(given_headers, dict):
                kwargs["extra_headers"] = given_headers
                extra_headers = given_headers
            # Remaining params go directly to litellm
            litellm_only = dict(extra_params)
            kwargs.update(extra_params)

        # A TLS setting of the provider's own -- a CA, a client certificate,
        # or the machines' mutual TLS -- goes in as a client made with it
        # (see _apply_tls). Nothing of its own: LiteLLM's global default.
        try:
            tls = _provider_tls(
                instance_id=instance_id,
                ssl_verify_mode=ssl_verify_mode,
                ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                ssl_client_cert_pem=ssl_client_cert_pem,
                ssl_client_key_pem=ssl_client_key_pem,
            )
        except _TlsRefusedError as exc:
            return _refusal(stream, str(exc))  # type: ignore[no-any-return]
        native = _native_kwargs(
            tls, provider_type=provider_type, instance_id=instance_id,
            ssl_verify_mode=ssl_verify_mode,
            ssl_ca_bundle_pem=ssl_ca_bundle_pem,
            ssl_client_cert_pem=ssl_client_cert_pem,
            ssl_client_key_pem=ssl_client_key_pem,
        )

        if str(kwargs["model"]) in _SYSTEM_FIRST:
            kwargs["messages"] = _system_first(kwargs["messages"]) or kwargs["messages"]
        kwargs = _remembered_effort(kwargs, str(kwargs["model"]))

        if stream:
            # Request token usage in the final streaming chunk (OpenAI-compatible).
            kwargs["stream_options"] = {"include_usage": True}
            if self._goes_direct(provider_type, kwargs, tls=tls, litellm_only=litellm_only):
                http = upstream_tls.clients.http(tls) if tls is not None else self._http
                return self._stream_direct(
                    kwargs, extra_headers, http=http, first_token_timeout_sec=first_token_timeout_sec,
                )
            _apply_tls(kwargs, tls, provider_type=provider_type, instance_id=instance_id, native=native)
            return self._stream_completion(**_for_litellm(kwargs, provider_type))

        _apply_tls(kwargs, tls, provider_type=provider_type, instance_id=instance_id, native=native)
        return await self._whole_answer(_for_litellm(kwargs, provider_type))

    def _goes_direct(
        self,
        provider_type: ProviderType,
        kwargs: dict[str, Any],
        *,
        tls: upstream_tls.UpstreamTls | None,
        litellm_only: dict[str, Any],
    ) -> bool:
        """Whether a streamed chat can skip LiteLLM: vLLM, as a plain OpenAI server.

        Everything LiteLLM would do differently keeps LiteLLM: the default
        outbound CA bundle is a LiteLLM setting, and extra params beyond
        headers are LiteLLM call arguments. Remote OpenAI APIs stay too:
        LiteLLM rewrites some of their parameters per model. A TLS setting of
        the model's own -- the machines' mutual TLS above all -- does not: the
        stream goes over a client made with it.
        """
        if self._http is None or not settings.vllm_direct_stream:
            return False
        if provider_type != ProviderType.VLLM or litellm_only:
            return False
        base = str(kwargs.get("api_base") or "")
        if not base:
            return False
        if tls is not None:
            return True
        return not (base.startswith("https://") and settings.tls_outbound_default_ca_bundle)

    async def _stream_direct(
        self, kwargs: dict[str, Any], extra_headers: dict[str, Any], *, http: httpx.AsyncClient | None = None,
        first_token_timeout_sec: float | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream a chat from vLLM as the engine sends it, event by event.

        Through LiteLLM every token became pydantic objects and was dumped back
        to JSON, and LiteLLM's per-chunk ``async for`` over the OpenAI SDK's
        stream abandoned one async generator per token (each one a
        ``call_soon_threadsafe`` when collected): about 60 % of the gateway's
        CPU at 16 users (py-spy, 2026-09-25). vLLM already sends OpenAI's
        format, so its events go on untouched. They are only re-cut at event
        boundaries: a network read can split one event or carry several, and
        every step after this one -- PII restore, usage, the saved reply --
        reads whole events.

        Two waits: up to *first_token_timeout_sec* (else the gateway's
        default) for the reply to start, then up to ``litellm_request_timeout``
        between events. The engine sends nothing while a request waits in its
        queue, so the first wait is the one a busy model needs to be long.
        """
        client = http or self._http
        assert client is not None  # noqa: S101 - _goes_direct checked it
        url = f"{str(kwargs['api_base']).rstrip('/')}/chat/completions"
        body = {key: value for key, value in kwargs.items() if key in _DIRECT_BODY_KEYS}
        body["model"] = str(kwargs["model"]).removeprefix("openai/")
        headers = {"Authorization": f"Bearer {kwargs.get('api_key') or 'EMPTY'}", **extra_headers}
        first_wait = first_token_timeout_sec or settings.first_token_timeout_sec
        between = settings.litellm_request_timeout
        # The socket may stay silent for the longer of the two; the waits
        # themselves are kept below, around each step.
        timeout = httpx.Timeout(connect=10.0, read=max(first_wait, between), write=10.0, pool=10.0)
        loop = asyncio.get_running_loop()
        closed = False
        tries = 0
        started = False
        try:
            while True:
                starts_by = loop.time() + first_wait
                async with AsyncExitStack() as stack:
                    # Ray Serve sends the headers with the first event, so the
                    # first wait covers both.
                    async with asyncio.timeout_at(starts_by):
                        response = await stack.enter_async_context(
                            client.stream("POST", url, json=body, headers=headers, timeout=timeout),
                        )
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:2000]
                        again = None if tries == _RETRIES else _try_again(
                            response.status_code, detail, body, str(kwargs["model"]),
                        )
                        if again is not None:
                            # Refused before a byte went to the client: again,
                            # with what was refused changed.
                            body, tries = again, tries + 1
                            continue
                        logger.warning("vLLM refused a streamed chat: %s %s", response.status_code, detail)
                        refusal = f"upstream {response.status_code}: {detail}"
                        yield _sse(_error_payload("server_error", refusal))
                        yield _DONE
                        return
                    events = await stack.enter_async_context(aclosing(_whole_events(response)))
                    while True:
                        # Only the wait for the next event is timed, never the
                        # yield: the caller's time is its own.
                        try:
                            if started:
                                async with asyncio.timeout(between):
                                    event = await anext(events)
                            else:
                                async with asyncio.timeout_at(starts_by):
                                    event = await anext(events)
                        except StopAsyncIteration:
                            break
                        started = True
                        yield f"{event}\n\n".encode()
                        if _is_done(event):
                            closed = True
                            break
                break
        except (TimeoutError, httpx.ReadTimeout):
            # Said as what it is, so the person whose reply failed knows whether
            # to try again (a busy model) or to report it (one that went quiet).
            if started:
                message = f"The model stopped sending its reply for {between:g} s."
            else:
                message = f"The model did not start its reply within {first_wait:g} s; it may be busy."
            logger.warning("Direct vLLM stream timed out: %s", message)
            yield _sse(_error_payload("server_error", message))
            yield _DONE
            return
        except (httpx.HTTPError, ssl.SSLError) as exc:
            # A TLS alert after the handshake -- a proxy refusing this client's
            # certificate, under TLS 1.3 -- arrives on the first read, unmapped.
            logger.exception("Direct vLLM stream failed")
            yield _sse(_error_payload("server_error", str(exc) or type(exc).__name__))
            yield _DONE
            return
        if not closed:
            yield _DONE

    async def _whole_answer(self, kwargs: dict[str, Any]) -> CompletionResult:
        """A chat that is not streamed, sent again after a refusal one change may cure (see _try_again)."""
        result = await self._non_stream_completion(**kwargs)
        for _ in range(_RETRIES):
            again = None if result.status_code < 400 else _try_again(
                result.status_code, json.dumps(result.payload), kwargs, str(kwargs["model"]),
            )
            if again is None:
                break
            kwargs = again
            result = await self._non_stream_completion(**kwargs)
        return result

    async def _non_stream_completion(self, **kwargs: Any) -> CompletionResult:
        """Execute a non-streaming completion.

        The whole answer has to be written before a byte comes back, so the wait is the one for a whole
        answer, not LiteLLM's 60 s: a model that thinks first ran past it on drafting a skill, and LiteLLM
        gave up and asked again, three times.
        """
        kwargs.setdefault("timeout", settings.non_stream_timeout_sec)
        try:
            response = await litellm.acompletion(**kwargs)
            # LiteLLM returns a ModelResponse — convert to dict
            payload = response.model_dump()  # type: ignore[union-attr]
            return CompletionResult(status_code=200, payload=payload)
        except litellm.exceptions.AuthenticationError as exc:
            return CompletionResult(
                status_code=401,
                payload=_error_payload("authentication_error", str(exc)),
            )
        except litellm.exceptions.RateLimitError as exc:
            return CompletionResult(
                status_code=429,
                payload=_error_payload("rate_limit_error", str(exc)),
            )
        except litellm.exceptions.BadRequestError as exc:
            return CompletionResult(
                status_code=400,
                payload=_error_payload("invalid_request_error", str(exc)),
            )
        except Exception as exc:
            logger.exception("LiteLLM completion failed")
            return CompletionResult(
                status_code=502,
                payload=_error_payload("server_error", str(exc)),
            )

    async def _stream_completion(self, **kwargs: Any) -> AsyncIterator[bytes]:
        """Execute a streaming completion, yielding SSE-encoded bytes."""
        try:
            tries = 0
            while True:
                try:
                    response = await litellm.acompletion(**kwargs)
                    break
                except litellm.exceptions.BadRequestError as refused:
                    again = None if tries == _RETRIES else _try_again(
                        400, str(refused), kwargs, str(kwargs["model"]),
                    )
                    if again is None:
                        raise
                    kwargs, tries = again, tries + 1
        except Exception as exc:
            # Pre-stream failure — yield the error as an SSE event so the
            # client receives a structured error instead of a broken stream.
            logger.exception("LiteLLM streaming failed (pre-stream)")
            error_data = _error_payload("server_error", str(exc))
            yield f"data: {json.dumps(error_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        try:
            async for chunk in response:  # type: ignore[union-attr]
                data = openai_chunk(chunk.model_dump())  # type: ignore[union-attr]
                yield f"data: {json.dumps(data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            logger.exception("LiteLLM streaming failed (mid-stream)")
            error_data = _error_payload("server_error", str(exc))
            yield f"data: {json.dumps(error_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"

    async def embedding(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        payload: dict[str, Any],
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
    ) -> CompletionResult:
        """Run an embedding request."""
        model_name = _build_litellm_model_name(
            provider_type=provider_type,
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            requested_model=payload.get("model", ""),
        )
        api_key = _resolve_api_key(api_key_encrypted)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "input": payload.get("input", ""),
        }
        # Same upstream resolution as completion(): embeddings on a
        # proxied OpenAI-compatible server need the identical /v1 and
        # api_key handling as the chat path.
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=api_key,
                provider_type=provider_type,
            ),
        )
        if extra_params:
            kwargs.update(extra_params)

        try:
            tls = _provider_tls(
                instance_id=instance_id,
                ssl_verify_mode=ssl_verify_mode,
                ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                ssl_client_cert_pem=ssl_client_cert_pem,
                ssl_client_key_pem=ssl_client_key_pem,
            )
        except _TlsRefusedError as exc:
            return CompletionResult(status_code=503, payload=_error_payload("server_error", str(exc)))
        native = _native_kwargs(
            tls, provider_type=provider_type, instance_id=instance_id,
            ssl_verify_mode=ssl_verify_mode,
            ssl_ca_bundle_pem=ssl_ca_bundle_pem,
            ssl_client_cert_pem=ssl_client_cert_pem,
            ssl_client_key_pem=ssl_client_key_pem,
        )
        _apply_tls(kwargs, tls, provider_type=provider_type, instance_id=instance_id, native=native)

        # The same mapping as a completion's: an embeddings request to a model
        # that cannot embed is the caller's mistake (400), not a gateway fault
        # (502), which is what it was reported as.
        try:
            response = await litellm.aembedding(**kwargs)
            return CompletionResult(
                status_code=200,
                payload=response.model_dump(),  # type: ignore[union-attr]
            )
        except litellm.exceptions.AuthenticationError as exc:
            return CompletionResult(status_code=401, payload=_error_payload("authentication_error", str(exc)))
        except litellm.exceptions.RateLimitError as exc:
            return CompletionResult(status_code=429, payload=_error_payload("rate_limit_error", str(exc)))
        except litellm.exceptions.BadRequestError as exc:
            return CompletionResult(status_code=400, payload=_error_payload("invalid_request_error", str(exc)))
        except Exception as exc:
            logger.exception("LiteLLM embedding failed")
            return CompletionResult(
                status_code=502,
                payload=_error_payload("server_error", str(exc)),
            )


    async def image_generation(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        payload: dict[str, Any],
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
    ) -> CompletionResult:
        """Make pictures from a prompt: an OpenAI ``/v1/images/generations``, answered as ``b64_json``.

        A local image server (vLLM-Omni, Fadenstack's own image app) takes the OpenAI shape and the
        diffusion settings beside it (steps, guidance, seed, negative prompt), which go as the body's
        extra fields. Pictures come back in the answer, never as links to fetch.
        """
        model_name = _build_litellm_model_name(
            provider_type=provider_type,
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            requested_model=payload.get("model", ""),
        )
        kwargs: dict[str, Any] = {
            "model": model_name,
            "prompt": payload.get("prompt", ""),
            "response_format": "b64_json",
        }
        for key in ("size", "n", "quality", "style"):
            if payload.get(key) is not None:
                kwargs[key] = payload[key]
        diffusion = {
            k: payload[k]
            for k in ("negative_prompt", "num_inference_steps", "guidance_scale", "seed")
            if payload.get(k) is not None
        }
        if diffusion:
            kwargs["extra_body"] = diffusion
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=_resolve_api_key(api_key_encrypted),
                provider_type=provider_type,
            ),
        )
        if extra_params:
            kwargs.update(extra_params)
        try:
            tls = _provider_tls(
                instance_id=instance_id,
                ssl_verify_mode=ssl_verify_mode,
                ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                ssl_client_cert_pem=ssl_client_cert_pem,
                ssl_client_key_pem=ssl_client_key_pem,
            )
        except _TlsRefusedError as exc:
            return CompletionResult(status_code=503, payload=_error_payload("server_error", str(exc)))
        native = _native_kwargs(
            tls, provider_type=provider_type, instance_id=instance_id,
            ssl_verify_mode=ssl_verify_mode,
            ssl_ca_bundle_pem=ssl_ca_bundle_pem,
            ssl_client_cert_pem=ssl_client_cert_pem,
            ssl_client_key_pem=ssl_client_key_pem,
        )
        _apply_tls(kwargs, tls, provider_type=provider_type, instance_id=instance_id, native=native)
        try:
            response = await litellm.aimage_generation(**kwargs)
            return CompletionResult(status_code=200, payload=response.model_dump())  # type: ignore[union-attr]
        except litellm.exceptions.AuthenticationError as exc:
            return CompletionResult(status_code=401, payload=_error_payload("authentication_error", str(exc)))
        except litellm.exceptions.RateLimitError as exc:
            return CompletionResult(status_code=429, payload=_error_payload("rate_limit_error", str(exc)))
        except litellm.exceptions.BadRequestError as exc:
            return CompletionResult(
                status_code=400, payload=_error_payload("invalid_request_error", str(exc)),
            )
        except Exception as exc:
            logger.exception("LiteLLM image generation failed")
            return CompletionResult(status_code=502, payload=_error_payload("server_error", str(exc)))

    async def rerank(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        requested_model: str,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
    ) -> CompletionResult:
        """Score *documents* against *query*: ``results`` of ``index`` and ``relevance_score``.

        Local engines and OpenAI-compatible servers serve the Cohere / Jina
        shape at ``/v1/rerank`` (vLLM, llama.cpp, Infinity), which LiteLLM
        reaches as ``hosted_vllm``; its ``openai`` provider, which chat and
        embeddings use, has no rerank. A remote API goes by its own provider
        (``cohere``, ``jina_ai``, ...). The documents are not asked back: the
        gateway has them, as the client sent them.
        """
        model = litellm_model or requested_model
        if provider_type in _RERANK_AS_VLLM and not litellm_provider:
            model_name = f"hosted_vllm/{model}"
        else:
            model_name = _build_litellm_model_name(
                provider_type=provider_type,
                litellm_provider=litellm_provider,
                litellm_model=litellm_model,
                requested_model=requested_model,
            )
        kwargs: dict[str, Any] = {
            "model": model_name,
            "query": query,
            "documents": documents,
            "return_documents": False,
        }
        if top_n is not None:
            kwargs["top_n"] = top_n
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=_resolve_api_key(api_key_encrypted),
                provider_type=provider_type,
            ),
        )
        if extra_params:
            kwargs.update(extra_params)
        try:
            tls = _provider_tls(
                instance_id=instance_id,
                ssl_verify_mode=ssl_verify_mode,
                ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                ssl_client_cert_pem=ssl_client_cert_pem,
                ssl_client_key_pem=ssl_client_key_pem,
            )
        except _TlsRefusedError as exc:
            return CompletionResult(status_code=503, payload=_error_payload("server_error", str(exc)))
        if tls is not None:
            # Rerank goes through LiteLLM's own HTTP handler for every
            # provider, and that takes a ready-made one.
            kwargs["client"] = upstream_tls.clients.handler(tls)
        try:
            response = await litellm.arerank(**kwargs)
            return CompletionResult(status_code=200, payload=response.model_dump())  # type: ignore[union-attr]
        except litellm.exceptions.AuthenticationError as exc:
            return CompletionResult(status_code=401, payload=_error_payload("authentication_error", str(exc)))
        except litellm.exceptions.RateLimitError as exc:
            return CompletionResult(status_code=429, payload=_error_payload("rate_limit_error", str(exc)))
        except litellm.exceptions.BadRequestError as exc:
            return CompletionResult(status_code=400, payload=_error_payload("invalid_request_error", str(exc)))
        except Exception as exc:
            logger.exception("LiteLLM rerank failed")
            return CompletionResult(status_code=502, payload=_error_payload("server_error", str(exc)))


#: Providers whose rerank API is the Cohere / Jina shape at ``/v1/rerank``.
_RERANK_AS_VLLM = (
    ProviderType.VLLM,
    ProviderType.LLAMACPP,
    ProviderType.REMOTE_OPENAI,
    ProviderType.REMOTE_CUSTOM,
)


def _error_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "type": error_type,
            "message": message,
            "param": None,
            "code": error_type,
        },
    }
