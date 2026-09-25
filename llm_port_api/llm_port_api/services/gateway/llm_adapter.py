"""LiteLLM adapter — unified LLM completion / embedding interface.

Wraps ``litellm.acompletion`` and ``litellm.aembedding`` to provide a
provider-agnostic calling layer.  The gateway service delegates all
upstream calls through this adapter instead of the raw HTTP proxy.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx
import litellm

from llm_port_api.db.crypto import decrypt_value
from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway.ssl_helpers import build_ssl_kwargs
from llm_port_api.settings import settings

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
#: What the body of a direct streamed chat is made of.
_DIRECT_BODY_KEYS = frozenset({"messages", "stream", "stream_options", *_OPENAI_PARAMS})
_DONE = b"data: [DONE]\n\n"


# Models whose chat template refused a system message after the conversation
# had begun: theirs go up front from then on (see _late_system_refused).
_SYSTEM_FIRST: set[str] = set()
_TEMPLATE_REFUSAL = re.compile(r"template|roles? must alternate|system (role|message)", re.IGNORECASE)


def _system_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """*messages* with each system message after the leading ones moved up to them.

    None when there is none to move. The order among the moved messages, and
    among the others, is kept.
    """
    lead = 0
    while lead < len(messages) and messages[lead].get("role") == "system":
        lead += 1
    late = [m for m in messages[lead:] if m.get("role") == "system"]
    if not late:
        return None
    rest = [m for m in messages[lead:] if m.get("role") != "system"]
    return [*messages[:lead], *late, *rest]


def _late_system_refused(
    status: int, detail: str, messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """The messages to try again with, when a template refused a late system message.

    The gateway puts a turn's skills after the history, so the prefix vLLM
    caches stays the same (``session_skills_position``). Qwen's and Llama 3's
    templates take a system message anywhere; one that does not answers 400
    with the template's error. That request then goes again with its system
    messages up front.
    """
    if status != 400 or not _TEMPLATE_REFUSAL.search(detail):
        return None
    return _system_first(messages)


def _remember_system_first(model: str) -> None:
    """Send *model*'s system messages up front from now on; say so once."""
    if model not in _SYSTEM_FIRST:
        _SYSTEM_FIRST.add(model)
        logger.warning(
            "%s's chat template refuses a system message after the conversation has begun; "
            "its skills go before the history from now on",
            model,
        )


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
        v1_prefixed = (
            ProviderType.VLLM,
            ProviderType.TGI,
            ProviderType.REMOTE_OPENAI,
        )
        if provider_type in v1_prefixed and not effective_base.endswith("/v1"):
            effective_base += "/v1"
        kwargs["api_base"] = effective_base
    if api_key:
        kwargs["api_key"] = api_key
    elif kwargs.get("api_base"):
        kwargs["api_key"] = "EMPTY"
    return kwargs


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
    ) -> CompletionResult | AsyncIterator[Any]:
        """Run a chat completion (streaming or non-streaming).

        Returns ``CompletionResult`` for non-streaming, or an async
        iterator of ``ModelResponse`` chunks for streaming.
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
            )
        )

        # Pass through supported OpenAI params
        for key in _OPENAI_PARAMS:
            if key in payload:
                kwargs[key] = payload[key]

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

        # Apply per-provider TLS overrides (CA bundle / mTLS).  When the
        # provider has nothing configured this is a no-op and LiteLLM
        # inherits the global default set in lifespan.
        tls: dict[str, Any] = {}
        if instance_id is not None:
            tls = build_ssl_kwargs(
                instance_id=instance_id,
                ssl_verify_mode=ssl_verify_mode,
                ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                ssl_client_cert_pem=ssl_client_cert_pem,
                ssl_client_key_pem=ssl_client_key_pem,
            )
            kwargs.update(tls)

        if str(kwargs["model"]) in _SYSTEM_FIRST:
            kwargs["messages"] = _system_first(kwargs["messages"]) or kwargs["messages"]

        if stream:
            # Request token usage in the final streaming chunk (OpenAI-compatible).
            kwargs["stream_options"] = {"include_usage": True}
            if self._goes_direct(provider_type, kwargs, tls=tls, litellm_only=litellm_only):
                return self._stream_direct(kwargs, extra_headers)
            return self._stream_completion(**kwargs)

        result = await self._non_stream_completion(**kwargs)
        moved = _late_system_refused(result.status_code, json.dumps(result.payload), kwargs["messages"])
        if moved is not None:
            _remember_system_first(str(kwargs["model"]))
            result = await self._non_stream_completion(**{**kwargs, "messages": moved})
        return result

    def _goes_direct(
        self,
        provider_type: ProviderType,
        kwargs: dict[str, Any],
        *,
        tls: dict[str, Any],
        litellm_only: dict[str, Any],
    ) -> bool:
        """Whether a streamed chat can skip LiteLLM: vLLM, as a plain OpenAI server.

        Everything LiteLLM would do differently keeps LiteLLM: per-provider
        TLS and the default outbound CA bundle are LiteLLM settings, and extra
        params beyond headers are LiteLLM call arguments. Remote OpenAI APIs
        stay too: LiteLLM rewrites some of their parameters per model.
        """
        if self._http is None or not settings.vllm_direct_stream:
            return False
        if provider_type != ProviderType.VLLM or tls or litellm_only:
            return False
        base = str(kwargs.get("api_base") or "")
        if not base:
            return False
        return not (base.startswith("https://") and settings.tls_outbound_default_ca_bundle)

    async def _stream_direct(
        self, kwargs: dict[str, Any], extra_headers: dict[str, Any],
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
        """
        assert self._http is not None  # noqa: S101 - _goes_direct checked it
        url = f"{str(kwargs['api_base']).rstrip('/')}/chat/completions"
        body = {key: value for key, value in kwargs.items() if key in _DIRECT_BODY_KEYS}
        body["model"] = str(kwargs["model"]).removeprefix("openai/")
        headers = {"Authorization": f"Bearer {kwargs.get('api_key') or 'EMPTY'}", **extra_headers}
        timeout = httpx.Timeout(connect=10.0, read=settings.litellm_request_timeout, write=10.0, pool=10.0)
        closed = False
        retried = False
        try:
            while True:
                request = self._http.stream("POST", url, json=body, headers=headers, timeout=timeout)
                async with request as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:2000]
                        moved = None if retried else _late_system_refused(
                            response.status_code, detail, body["messages"],
                        )
                        if moved is not None:
                            # Refused before a byte went to the client: again,
                            # with the system messages up front.
                            _remember_system_first(str(kwargs["model"]))
                            body = {**body, "messages": moved}
                            retried = True
                            continue
                        logger.warning("vLLM refused a streamed chat: %s %s", response.status_code, detail)
                        refusal = f"upstream {response.status_code}: {detail}"
                        yield _sse(_error_payload("server_error", refusal))
                        yield _DONE
                        return
                    async with aclosing(_whole_events(response)) as events:
                        async for event in events:
                            yield f"{event}\n\n".encode()
                            if _is_done(event):
                                closed = True
                                break
                break
        except httpx.HTTPError as exc:
            logger.exception("Direct vLLM stream failed")
            yield _sse(_error_payload("server_error", str(exc) or type(exc).__name__))
            yield _DONE
            return
        if not closed:
            yield _DONE

    async def _non_stream_completion(self, **kwargs: Any) -> CompletionResult:
        """Execute a non-streaming completion."""
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
            try:
                response = await litellm.acompletion(**kwargs)
            except litellm.exceptions.BadRequestError as refused:
                moved = _late_system_refused(400, str(refused), kwargs.get("messages") or [])
                if moved is None:
                    raise
                _remember_system_first(str(kwargs["model"]))
                response = await litellm.acompletion(**{**kwargs, "messages": moved})
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
                data = chunk.model_dump()  # type: ignore[union-attr]
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
            )
        )
        if extra_params:
            kwargs.update(extra_params)

        if instance_id is not None:
            kwargs.update(
                build_ssl_kwargs(
                    instance_id=instance_id,
                    ssl_verify_mode=ssl_verify_mode,
                    ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                    ssl_client_cert_pem=ssl_client_cert_pem,
                    ssl_client_key_pem=ssl_client_key_pem,
                ),
            )

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
            )
        )
        if extra_params:
            kwargs.update(extra_params)
        if instance_id is not None:
            kwargs.update(
                build_ssl_kwargs(
                    instance_id=instance_id,
                    ssl_verify_mode=ssl_verify_mode,
                    ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                    ssl_client_cert_pem=ssl_client_cert_pem,
                    ssl_client_key_pem=ssl_client_key_pem,
                ),
            )
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
