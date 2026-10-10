from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, TypedDict

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import (
    TURN_INTERRUPTED_ERROR,
    TURNS_RETRYABLE,
    ChatMessage,
    PrivacyMode,
    ProviderType,
    TurnStatus,
)
from faden_gateway.services.gateway.audit import AuditService
from faden_gateway.services.gateway.usage_group import UsageGroupResolver
from faden_gateway.services.gateway.auth import AuthContext
from faden_gateway.services.gateway import (
    attachment_search,
    faden_events,
    knowledge,
    media_fetch,
    skill_files,
    system_tools,
)
from faden_gateway.services.extensions import registry as extension_registry
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import tool_classes
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.llm_adapter import LLMAdapter, reasoning_of
from faden_gateway.services.gateway.observability import (
    GatewayObservability,
)
from faden_gateway.services.gateway.mcp_client import MCPClient
from faden_gateway.services.gateway.mcp_tool_cache import MCP_TOOL_PREFIX, MCPToolCache
from faden_gateway.services.gateway.pii_client import PIIClient
from faden_gateway.services.gateway.session_titles import title_from_message
from faden_gateway.services.gateway.pii_policy import PIIPolicy, SessionPIIOverride, resolve_session_pii
from faden_gateway.services.gateway.pii_restore import (
    redact_tokens,
    restore_payload,
    restore_sse,
    restore_tool_calls,
    restore_text,
)
from faden_gateway.services.gateway.proxy import UpstreamProxy, UpstreamResult
from faden_gateway.services.gateway.rag_lite_client import RagLiteClient
from faden_gateway.services.gateway.ratelimit import RateLimiter
from faden_gateway.services.gateway.routing import RouterService, RoutingDecision
from faden_gateway.services.gateway.skills_client import ResolvedSkill, SkillsClient
from faden_gateway.services.gateway.stream import StreamStats, wrap_sse_stream
from faden_gateway.services.gateway.stream_buffer import StreamBuffer
from faden_gateway.services.gateway.tool_stream import DONE, ToolCalls, sse, sse_events
from faden_gateway.services.gateway.usage import (
    estimate_input_tokens,
    usage_from_payload,
)
from faden_gateway.services.gateway.file_store import FileStore
from faden_gateway.services.gateway.tool_availability import ToolAvailabilityService
from faden_gateway.services.gateway.tool_router import ToolRouter
from faden_gateway.settings import settings

logger = logging.getLogger(__name__)


def _candidate_adapter_kwargs(candidate: Any) -> dict[str, Any]:
    """Return adapter kwargs derived from a routed provider candidate.

    Centralises the mapping of the per-instance provider/credential
    fields (including outbound TLS) into the kwargs accepted by
    :meth:`LLMAdapter.completion` and :meth:`LLMAdapter.embedding`.
    """
    return {
        "provider_type": candidate.provider_type,
        "base_url": candidate.base_url,
        "api_key_encrypted": candidate.api_key_encrypted,
        "litellm_provider": candidate.litellm_provider,
        "litellm_model": candidate.litellm_model,
        "extra_params": (
            dict(candidate.extra_params) if candidate.extra_params else None
        ),
        "instance_id": candidate.instance_id,
        "ssl_verify_mode": getattr(candidate, "ssl_verify_mode", None),
        "ssl_ca_bundle_pem": getattr(candidate, "ssl_ca_bundle_pem", None),
        "ssl_client_cert_pem": getattr(candidate, "ssl_client_cert_pem", None),
        "ssl_client_key_pem": getattr(candidate, "ssl_client_key_pem", None),
    }


def _first_token_wait(candidate: Any) -> float | None:
    """The model's own wait for a streamed reply to start, or None for the gateway's default.

    Set on its deployment and published in the routing row's capacity hints,
    which the gateway reads on every request: a change applies at once.
    """
    hints = getattr(candidate, "capacity_hints", None)
    value = hints.get("first_token_timeout_sec") if isinstance(hints, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


# ── PII context system prompts ───────────────────────────────────────────────
_PII_REDACT_SYSTEM_PROMPT = (
    "IMPORTANT — Privacy notice: The user's message has been processed by an "
    "automated PII (Personally Identifiable Information) redaction system. "
    "Certain sensitive values have been replaced with placeholders such as "
    "<EMAIL_ADDRESS>, <PHONE_NUMBER>, <PERSON>, "
    "<CREDIT_CARD>, etc. These placeholders indicate where real data "
    "existed but was removed for privacy. "
    "When responding, preserve these placeholders exactly as they appear — do "
    "not attempt to guess the original values. If the user asks you to recall "
    "or fill in a redacted value, politely explain that the information was "
    "redacted for privacy."
)

_PII_TOKENIZE_SYSTEM_PROMPT = (
    "IMPORTANT — Privacy notice: The user's message has been processed by an "
    "automated PII (Personally Identifiable Information) tokenization system. "
    "Certain sensitive values have been replaced with surrogate tokens such as "
    "[PERSON_1], [EMAIL_ADDRESS_1], [PHONE_NUMBER_1], [LOCATION_1], etc. "
    "Each token represents a real value that will be restored after your response. "
    "CRITICAL RULES:\n"
    "1. Treat each token as if it were the real value it represents. For example, "
    "[PERSON_1] is a real person — refer to them naturally, use appropriate "
    "pronouns, and reason about them as you would a real name.\n"
    "2. Preserve every token exactly as written in your response — do not modify, "
    "remove, decode, or invent the underlying value.\n"
    "3. Place tokens in the same logical positions in your answer (e.g. if the "
    "user asks 'Who is [PERSON_1]?', your answer should reference [PERSON_1]).\n"
    "4. If multiple tokens of the same type appear (e.g. [PERSON_1] and "
    "[PERSON_2]), treat them as distinct individuals/values.\n"
    "5. Do not mention or explain the tokenization system to the user."
)


@dataclass(slots=True, frozen=True)
class GatewayResponse:
    """Structured non-streaming gateway output."""

    status_code: int
    payload: dict[str, Any]
    provider_instance_id: str
    latency_ms: int
    trace_id: str | None = None


@dataclass(slots=True, frozen=True)
class StreamingGatewayResponse:
    """Structured streaming gateway output."""

    stream: AsyncIterator[bytes]
    provider_instance_id: str
    latency_ms: int
    stats: StreamStats
    trace_id: str | None = None
    #: The reply's row in the chat session, when there is one.
    turn_id: str | None = None


#: What each endpoint can be sent to: the kinds a route declares
#: (``node_metadata["task"]``), plus routes that declare none.
_ENDPOINT_KINDS: dict[str, set[str | None]] = {
    "/v1/chat/completions": {"chat", None},
    "/v1/embeddings": {"embeddings", None},
    "/v1/rerank": {"scoring", None},
    # Only a route that says it makes pictures: a route that says nothing is a chat model.
    "/v1/images/generations": {"images"},
}

_KIND_ADVICE = {
    "chat": "is a chat model: send it to /v1/chat/completions",
    "embeddings": "is an embeddings model: send it to /v1/embeddings",
    "scoring": "is a scoring (rerank) model: send it to /v1/rerank",
    "images": "is an image model: send it to /v1/images/generations",
}

#: Image parameters passed on to the image server as they came (the OpenAI shape, as vLLM-Omni takes it).
_IMAGE_FIELDS = (
    "size", "n", "negative_prompt", "num_inference_steps", "guidance_scale", "seed", "quality", "style",
)


def _image_trace(payload: dict[str, Any] | None) -> dict[str, Any]:
    """An image answer as traces and logs keep it: how many pictures and how big, never the pictures."""
    data = (payload or {}).get("data") or []
    return {
        "created": (payload or {}).get("created"),
        "data": [
            {"b64_json": f"<{len(str(d.get('b64_json') or ''))} characters>"} for d in data if isinstance(d, dict)
        ],
    }


def _as_input(rerank: dict[str, Any]) -> dict[str, Any]:
    """A rerank request as the pipeline knows texts: ``input``, query first.

    PII, the limits and the trace read ``messages`` or ``input`` -- a rerank
    request's ``query`` and ``documents`` would have gone out unscanned.
    """
    documents = [d if isinstance(d, str) else str(d.get("text", "")) for d in rerank["documents"]]
    return {"model": rerank["model"], "input": [rerank["query"], *documents]}


def _reranked(result: dict[str, Any], rerank: dict[str, Any], alias: str) -> dict[str, Any]:
    """The upstream's scores, with the client's own documents when it asked for them.

    The documents come from the request, not from upstream: they went out
    PII-scanned, and would have come back with its tokens.
    """
    results = []
    for item in result.get("results") or []:
        entry = {"index": item["index"], "relevance_score": item["relevance_score"]}
        if rerank.get("return_documents", True):
            document = rerank["documents"][item["index"]]
            entry["document"] = document if isinstance(document, dict) else {"text": document}
        results.append(entry)
    tokens = ((result.get("meta") or {}).get("billed_units") or {}).get("total_tokens") or 0
    return {
        "id": result.get("id"),
        "model": alias,
        "results": results,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


#: Qwen3-Reranker's instruction format, as its model card and vLLM's example
#: give it. The server expects the client to apply it; raw text ranked a
#: sentence about bananas first for "When is the Aurora launch?".
_QWEN3_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    'Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_QWEN3_INSTRUCTION = "Given a question, retrieve passages that answer the question"


def _in_model_format(candidate: Any, alias: str, query: str, documents: list[str]) -> tuple[str, list[str]]:
    """*query* and *documents* as the route's reranker expects them.

    Qwen3-Reranker gets its instruction format -- unless the query has it
    already: a client that formats for the model is not wrapped twice.
    """
    name = f"{getattr(candidate, 'litellm_model', None) or ''} {alias}".lower()
    if "qwen3" not in name or "rerank" not in name or query.startswith("<|im_start|>"):
        return query, documents
    return (
        f"{_QWEN3_PREFIX}<Instruct>: {_QWEN3_INSTRUCTION}\n<Query>: {query}\n",
        [f"<Document>: {d}{_QWEN3_SUFFIX}" for d in documents],
    )


def _rerank_routes(alias: str, candidates: list[Any]) -> list[Any]:
    """The routes that can take a rerank request.

    A model Fadenstack serves on a cluster runs under Ray Serve, whose API has
    ``/v1/score`` and no ``/v1/rerank``.
    """
    routes = [c for c in candidates if getattr(c, "source_kind", None) != "inference_deployment"]
    if not routes:
        raise GatewayError(
            status_code=400,
            message=f"{alias} runs on a cluster (Ray Serve), which serves no rerank API.",
            error_type="invalid_request_error",
            code="rerank_not_supported",
            param="model",
        )
    return routes


def _check_kind(endpoint: str, alias: str, candidates: list[Any]) -> None:
    """Refuse a request no route behind *alias* can serve, before sending it anywhere.

    With models of several kinds behind one gateway, a chat request to an
    embedding model went all the way to vLLM and came back as LiteLLM's
    "does not support Chat Completions API". The route says what it is; the
    gateway can say so first, in plain words, and not spend a slot on it.
    """
    allowed = _ENDPOINT_KINDS.get(endpoint)
    if allowed is None:
        return
    kinds = {(getattr(c, "node_metadata", None) or {}).get("task") for c in candidates}
    if not kinds or kinds & allowed:
        return
    # A route that says nothing is taken for a chat model (the endpoints that take such routes allow None).
    kind = next((k for k in kinds if k is not None), "chat")
    raise GatewayError(
        status_code=400,
        message=f"{alias} {_KIND_ADVICE.get(kind, f'serves {kind} requests, not this endpoint')}.",
        error_type="invalid_request_error",
        code="model_kind_mismatch",
        param="model",
    )


def _is_cloud(candidate: Any) -> bool:
    """Whether the route goes to a remote (cloud) provider."""
    return candidate.provider_type.value.startswith("remote_")


def _needs_scan(pii_policy: PIIPolicy | None, candidate: Any) -> bool:
    """Whether the PII policy scans what is sent to *candidate*."""
    if pii_policy is None:
        return False
    if _is_cloud(candidate):
        return pii_policy.egress.enabled_for_cloud
    return pii_policy.egress.enabled_for_local


#: Work started for after the response, kept referenced until it is done
#: (the event loop holds tasks only weakly).
_BACKGROUND: set[asyncio.Task[Any]] = set()


def _in_background(step: Awaitable[Any]) -> None:
    """Run *step* without waiting for it; a failure is logged, not raised."""

    async def run() -> None:
        try:
            await step
        except Exception:
            logger.warning("Background step failed", exc_info=True)

    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _kept(step: Awaitable[Any]) -> asyncio.Future[Any]:
    """Start *step* in a task of its own, kept referenced until it is done."""
    task = asyncio.ensure_future(step)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def _to_the_end(step: Awaitable[Any], *, until: asyncio.Event | None = None) -> None:
    """Start *step*, which goes on to its end even if this task is cancelled.

    A streamed response's last work ran in the response's own task, and
    Starlette cancels that task when the client disconnects. The step now runs
    in a task of its own, kept referenced until it is done; a cancelled caller
    stops waiting for it but does not stop it. The caller waits for the whole
    step, or only until *until* is set.
    """
    task = _kept(step)
    if until is None:
        await asyncio.shield(task)
        return
    # Set by the step itself; the task is watched too, so a step that dies
    # before setting it cannot leave the caller waiting.
    waiter = asyncio.ensure_future(until.wait())
    try:
        await asyncio.shield(asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED))
    finally:
        waiter.cancel()


#: The event that closes an OpenAI-style stream.
_DONE_EVENT = b"data: [DONE]\n\n"
_DONE_MARK = re.compile(rb"data:[ \t]*\[DONE\]")


def _split_done(chunk: bytes) -> tuple[bytes, bool]:
    """Split the closing ``[DONE]`` event off *chunk*: (what came before it, whether it was there).

    Chunks are the upstream's bytes as they arrived, so the last content and
    the ``[DONE]`` can share one; nothing follows ``[DONE]``.
    """
    found = _DONE_MARK.search(chunk)
    if found is None:
        return chunk, False
    return chunk[: found.start()], True


async def _together(*steps: Awaitable[Any]) -> list[Any]:
    """Await *steps* side by side; their results, in order.

    When one fails, the others are cancelled and its own exception is raised
    -- not an ExceptionGroup, which the routes do not handle.
    """
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(_awaited(step)) for step in steps]
    except BaseExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


async def _awaited(step: Awaitable[Any]) -> Any:
    return await step


async def _value(value: Any) -> Any:
    """*value*, as a step that needs no waiting."""
    return value


def _scan_units(content: str) -> tuple[list[str], Callable[[list[str]], str]]:
    """A tool answer as the texts it is scanned as, and how to put it back together.

    A JSON answer is its string values, one text each, rebuilt in place: the
    structure is never handed to the scanner, which could break it. Any
    other answer is one text.
    """
    try:
        data = json.loads(content)
    except ValueError:
        return [content], lambda scanned: scanned[0]
    if not isinstance(data, dict | list):
        return [content], lambda scanned: scanned[0]
    places: list[tuple[Any, Any]] = []

    def collect(node: Any) -> None:
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if isinstance(value, str):
                places.append((node, key))
            elif isinstance(value, dict | list):
                collect(value)

    collect(data)

    def rebuild(scanned: list[str]) -> str:
        for (node, key), text in zip(places, scanned, strict=True):
            node[key] = text
        return json.dumps(data)

    return [node[key] for node, key in places], rebuild


def _last_user_text(payload: dict[str, Any]) -> str:
    """The text of the last user message: what RAG and skills look up."""
    for msg in reversed(payload.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            return ""
    return ""


def _with_tools(payload: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """*payload* offering *tools* too; one of the same name is not added twice."""
    present = {(t.get("function") or {}).get("name") for t in payload.get("tools") or []}
    added = [t for t in tools if t["function"]["name"] not in present]
    out = {**payload, "tools": [*(payload.get("tools") or []), *added]}
    out.setdefault("tool_choice", "auto")
    return out


def _tool_definition_name(tool: dict[str, Any]) -> str:
    return (tool.get("function") or {}).get("name") or ""


#: The gateway's tools offered after the MCP tools.
_AFTER_MCP_TOOLS = knowledge.NAMES | {attachment_search.NAME, skill_files.NAME}


def _with_mcp_tools(payload: dict[str, Any], mcp_tools: list[dict[str, Any]]) -> dict[str, Any]:
    """*payload* offering *mcp_tools* as its MCP tools, where they stood.

    They go where the first request put them, in the catalog's order, so a
    tool switched on changes the prompt's tool list at one place only.
    """
    tools = [
        t for t in payload.get("tools") or [] if not _tool_definition_name(t).startswith(MCP_TOOL_PREFIX)
    ]
    at = next((i for i, t in enumerate(tools) if _tool_definition_name(t) in _AFTER_MCP_TOOLS), len(tools))
    block = [t.get("openai_tool", t) for t in mcp_tools]
    return {**payload, "tools": [*tools[:at], *block, *tools[at:]]}


def _switched_on(req: _Prepared) -> list[dict[str, Any]]:
    """The MCP tools a request offers, in the catalog's order."""
    return [t for t in req.mcp_findable if t.get("qualified_name") in req.mcp_on]


def _skills_allow(skills: list[ResolvedSkill], name: str) -> bool:
    """Whether the request's skills let the model use tool *name*.

    A skill's ``forbidden_tools`` are out; when any skill lists
    ``allowed_tools``, only the tools they list are in -- and the system
    tools, which do nothing outside the gateway.
    """
    if any(name in (skill.forbidden_tools or ()) for skill in skills):
        return False
    allowed = [skill.allowed_tools for skill in skills if skill.allowed_tools]
    return not allowed or name in system_tools.NAMES or any(name in names for names in allowed)


#: Names the gateway's own tools answer to.
_GATEWAY_TOOLS = system_tools.NAMES | knowledge.NAMES | {attachment_search.NAME, skill_files.NAME}


def _short(name: str) -> str:
    """A tool's name without its prefix, "-" and "_" and case: "getsum" for "mcp.everything.get-sum"."""
    return re.sub(r"[-_\s]", "", name.split(".", 2)[-1] if name.startswith(MCP_TOOL_PREFIX) else name).lower()


def _mcp_name(req: _Prepared, name: str) -> str:
    """*name*, or the MCP tool it names without its prefix, when one tool the chat may use has that name.

    A model told "use the get-sum tool" calls "get-sum", or "get_sum".
    """
    if name.startswith(MCP_TOOL_PREFIX) or name in _GATEWAY_TOOLS or not req.system_tools:
        return name
    same = [
        qualified for t in req.mcp_findable
        if _short(qualified := t.get("qualified_name", "")) == _short(name)
    ]
    return same[0] if len(same) == 1 else name


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


# A name as tools are named -- letters, digits, "_", "." and "-" -- up to what
# some models append ("knowledge_search<|channel|>commentary"). Without "-",
# "mcp.everything.get-sum" was cut to "mcp.everything.get": every hyphenated
# MCP tool failed as unknown (dev stack load test, 2026-09-26).
_TOOL_NAME = re.compile(r"^[\w.-]+")


def _tool_name(call: dict[str, Any]) -> str:
    """A call's tool name, without what some models append to it."""
    raw = (call.get("function") or {}).get("name") or ""
    match = _TOOL_NAME.match(raw)
    return match.group(0) if match else raw


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _restored(value: Any, mapping: dict[str, str]) -> Any:
    """*value* with PII tokens put back, in every string it holds."""
    if isinstance(value, str):
        return restore_text(value, mapping)
    if isinstance(value, list):
        return [_restored(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _restored(v, mapping) for k, v in value.items()}
    return value


def _exchange_record(exchange: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """A turn's tool exchange as the reply keeps it, each tool answer capped.

    The saved history held the final text only, never the tool calls and
    results that led to it, so the model saw tool requests answered in plain
    text and stopped calling tools: on the dev stack every chat called one on
    its first turn and none from its third (2026-09-26). The context
    assembler replays this before the reply.

    Kept as the model saw it -- tokens and redactions of a PII policy in
    place, never the values put back -- since the PII scan reads message
    content and not a tool call's arguments.
    """
    if not exchange:
        return None
    cap = settings.session_tool_result_max_chars
    kept: list[dict[str, Any]] = []
    for message in exchange:
        content = message.get("content")
        long_answer = message.get("role") == "tool" and isinstance(content, str) and len(content) > cap
        kept.append({**message, "content": content[:cap] + " [truncated]"} if long_answer else message)
    return {"exchange": kept}


def _replayed_tool_message(message: dict[str, Any]) -> bool:
    """Whether *message* is part of a tool exchange: a tool answer, or the calls."""
    role = message.get("role")
    return role == "tool" or (role == "assistant" and bool(message.get("tool_calls")))


def _without_replayed_tools(payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* without the tool exchanges its history replays, when it offers no tools.

    A chat template may refuse tool messages in a request without tools. Only
    the history -- what comes before the last user message -- is touched: a
    client running its own tools sends its calls and results after it.
    """
    if payload.get("tools"):
        return payload
    messages = list(payload.get("messages") or [])
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    kept = [m for i, m in enumerate(messages) if i > last_user or not _replayed_tool_message(m)]
    return payload if len(kept) == len(messages) else {**payload, "messages": kept}


def _assistant_turn(content: str | None, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The model's tool-calling turn, as it goes back to the model with the results.

    Each call carries the arguments the tool was run with, as JSON. A model
    that wrote them malformed (``{"a": 17, "b": }``) had them sent back as
    written, and vLLM, which parses them for the chat template, refused the
    next round: the chat got an error for a reply, and with the exchange kept
    in the history every later turn of it would have been refused too.
    """
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {**call, "function": {**(call.get("function") or {}), "arguments": _json_arguments(call)}}
            for call in calls
        ],
    }


def _cap_output(payload: dict[str, Any]) -> None:
    """Cap a chat's reply at ``session_max_output_tokens`` when it sets no cap.

    The chat page sets none, and a model that loops ran to its context limit:
    one reply of 31,136 tokens took 212 s of a model slot, and at 146 KB it
    pushed the rest of the chat's history out of every later turn (dev
    stack, 2026-09-26). Only a chat with a session gets it; an API request
    without one keeps OpenAI's meaning, no cap.
    """
    cap = settings.session_max_output_tokens
    if cap and "max_tokens" not in payload and "max_completion_tokens" not in payload:
        payload["max_tokens"] = cap


def _allowing_personal_data(mcp_tools: list[dict[str, Any]] | None) -> set[str]:
    """The MCP tools whose server is set to allow personal data."""
    return {t.get("qualified_name") for t in mcp_tools or [] if t.get("pii_mode") == "allow"}


def _pii_events(req: _Prepared) -> list[dict[str, Any]]:
    """What goes before the answer, when the client asked for events.

    The PII notice (when something was found), and the client's tools a rule
    refused.
    """
    if not req.faden_events:
        return []
    notice = (
        faden_events.pii_notice(req.pii_types, req.pii_policy.egress.mode) if req.pii_policy is not None else None
    )
    return [*([notice] if notice is not None else []), *req.faden_leading]


async def _leading(events: list[dict[str, Any]], stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """*stream*, after *events* as named SSE events."""
    for event in events:
        yield faden_events.sse_event(event)
    async for chunk in stream:
        yield chunk


def _replayable(messages: list[dict[str, Any]], mapping: dict[str, str] | None) -> list[dict[str, Any]]:
    """A gateway round as the client keeps it: PII values put back, arguments as valid JSON.

    The client sends it back as history; the next request scans it again.
    """
    if not mapping:
        return messages
    out = []
    for message in messages:
        restored = dict(message)
        if isinstance(restored.get("content"), str):
            restored["content"] = restore_text(restored["content"], mapping)
        if isinstance(restored.get("tool_calls"), list):
            restored["tool_calls"] = restore_tool_calls(restored["tool_calls"], mapping)
        out.append(restored)
    return out


def _mcp_arguments(req: _Prepared, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A tool's arguments, with the values tokenize mode took out when its server allows them.

    A server set to allow personal data got the tokens: "add 17 and
    [DATE_TIME_1]" called get-sum with b="[DATE_TIME_1]". A server set to
    redact or block still gets the tokens.
    """
    if req.token_mapping and name in req.mcp_pii_allowed:
        return _restored(arguments, req.token_mapping)
    return arguments


def _json_arguments(call: dict[str, Any]) -> str:
    """A call's arguments as the tool got them, in JSON: ``{}`` when malformed."""
    return json.dumps(_tool_arguments(call))


def _turn_outcome(
    *, content: str, error: str | None, status_code: int, closing: bool, finish_reason: str | None,
    interrupted: bool = False,
) -> tuple[str, str | None]:
    """How a streamed reply ended, for its row in the chat: (status, error)."""
    if interrupted:  # the gateway went away while writing it
        return TurnStatus.INTERRUPTED.value, TURN_INTERRUPTED_ERROR
    if error:  # the model's server refused, or failed part way
        return TurnStatus.FAILED.value, error
    if status_code >= 500:
        return TurnStatus.FAILED.value, "The model's answer broke off."
    if not closing:  # the client left, or stopped it: what was written is kept
        return TurnStatus.STOPPED.value, None
    if not content and finish_reason != "tool_calls":
        return TurnStatus.FAILED.value, "The model sent no answer."
    return TurnStatus.COMPLETE.value, None


def _stream_outcome(stats: StreamStats, status_code: int, error_code: str | None) -> tuple[int, str | None]:
    """A stream's status and error code for the log, counting an error event.

    The model's server refused (a 400 from vLLM, say) and the client got
    that event inside a 200: it is logged as the failure it was.
    """
    if stats.error and status_code == 200:
        return 502, "upstream_refused"
    return status_code, error_code


def _mcp_server(name: str) -> str | None:
    parts = name.split(".", 2)
    return parts[1] if len(parts) >= 2 else None


def _add_usage(total: dict[str, int], usage: Any) -> None:
    """Add one round's token usage to the answer's."""
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(usage.get(key), int):
            total[key] = total.get(key, 0) + usage[key]


def _insert_after_system(payload: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """*payload* with *message* after its leading system messages."""
    messages = list(payload.get("messages") or [])
    at = 0
    while at < len(messages) and messages[at].get("role") == "system":
        at += 1
    messages.insert(at, message)
    return {**payload, "messages": messages}


def _insert_before_question(payload: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """*payload* with *message* after the history, just before the last user message.

    Where a turn's own context goes: what changes from turn to turn sits
    after what does not, so the prefix vLLM caches stays the same. With no
    user message it goes where the system messages end.
    """
    messages = list(payload.get("messages") or [])
    at = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), None)
    if at is None:
        return _insert_after_system(payload, message)
    messages.insert(at, message)
    return {**payload, "messages": messages}


def _file_types(names: list[tuple[str, str]]) -> list[str]:
    """File types as skill triggers name them ("pdf"), from names and media types."""
    types: set[str] = set()
    for filename, content_type in names:
        suffix = PurePosixPath((filename or "").replace("\\", "/")).suffix.lower().lstrip(".")
        if suffix:
            types.add(suffix)
            continue
        guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
        if guessed:
            types.add(guessed.lstrip(".").lower())
    return sorted(types)


def _stream_error_event(exc: BaseException) -> bytes:
    """An SSE error event, in the shape the OpenAI APIs send mid-stream."""
    if isinstance(exc, GatewayError):
        error = {"message": exc.message, "type": exc.error_type, "code": exc.code, "param": exc.param}
    else:
        error = {
            "message": "The answer was cut off: the model's stream failed.",
            "type": "server_error",
            "code": "upstream_stream_failed",
            "param": None,
        }
    return f"data: {json.dumps({'error': error})}\n\n".encode()


def _privacy_mode(policy: Any | None, req: _Prepared) -> Any:
    """What the trace may keep: for an agent, metadata only -- its sessions stay on the device."""
    if req.agent is not None:
        return PrivacyMode.METADATA_ONLY
    return policy.privacy_mode if policy else None


class ToolModeCheck:
    """The chat's tool mode, as a governance check on each tool call.

    Hidden tools are not offered (``_offer_by_mode``); one called anyway is
    refused here. A tool the mode asks about waits for the user.
    """

    name = "tool_mode"
    stages = frozenset({gov.GovernanceStage.TOOL})
    output_window_chars = 0

    def __init__(self, classifier: tool_classes.Classifier, mode: tool_classes.ToolMode) -> None:
        self.classifier = classifier
        self.mode = mode

    async def check(self, ctx: gov.GovernanceContext) -> gov.GovernanceDecision | None:
        tool = self.classifier.classify(ctx.tool_id or "")
        decision = tool_classes.decide(self.mode, tool)
        if decision is tool_classes.Decision.RUN:
            return None
        return gov.GovernanceDecision(
            outcome="block" if decision is tool_classes.Decision.HIDDEN else "approval_required",
            policy_id=f"tool_mode:{self.mode.value}:{tool.tool_class.value}",
            category="tool_mode",
            message=tool_classes.reason(self.mode, decision),
            policy_name=f"Tool mode {self.mode.value}",
        )


class _AgentLogFields(TypedDict, total=False):
    agent_id: str | None
    device_id: str | None
    conversation_id: str | None
    turn_id: str | None


def _agent_log_fields(req: _Prepared) -> _AgentLogFields:
    """The agent columns of the request log: which agent, device and conversation."""
    agent = req.agent
    if agent is None:
        return {}
    return {
        "agent_id": agent.id,
        "device_id": agent.device_id,
        "conversation_id": agent.conversation_id,
        "turn_id": agent.turn_id,
    }


def _agent_pii_override(req: _Prepared) -> SessionPIIOverride | None:
    """The PII mode an agent's conversation asks for (``X-Faden-PII-Mode``), as its override.

    An agent's conversation has no server session, so no stored override: the
    SDK sends the mode with each request, as it sends its tool mode. It is
    applied when the agent's setting (or, with none, the tenant's) allows it.
    """
    if req.agent is None or req.agent.pii_mode is None:
        return None
    return SessionPIIOverride(egress_mode=req.agent.pii_mode)


def _agent_tools(payload: dict[str, Any], req: _Prepared) -> dict[str, Any]:
    """Only the gateway tools the agent allows; the caller's own tools stay."""
    tools = payload.get("tools")
    if not tools or req.agent is None or req.agent.agent.server_tools is None:
        return payload
    kept = [
        tool for tool in tools
        if (name := _tool_definition_name(tool)) in req.client_tools or req.agent.allows_server_tool(name)
    ]
    if len(kept) == len(tools):
        return payload
    if not kept:
        return {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
    return {**payload, "tools": kept}


def _skills_used(skills: list[ResolvedSkill]) -> list[dict[str, Any]] | None:
    """The skills a request used, for the audit log.

    With the version's content hash: versions are never deleted or changed,
    so the entry names exactly what the model was told.
    """
    if not skills:
        return None
    return [
        {
            "skill_id": str(s.skill_id),
            "name": s.name,
            "slug": s.slug,
            "version": s.version,
            "content_sha256": s.content_sha256,
            "matched_by": s.matched_by,
        }
        for s in skills
    ]


@dataclass(slots=True)
class _Prepared:
    """What a request has gathered on its way to the model.

    Filled step by step, so that wherever it fails, the route can release the
    slot it holds and write down how far the request got.
    """

    payload: dict[str, Any]
    egress_payload: dict[str, Any] | None = None
    #: The media URLs the client sent, fetched by the gateway: (url, kind) -> data: URL (media_fetch.py).
    media: dict[tuple[str, str], str] = field(default_factory=dict)
    token_mapping: dict[str, str] | None = None
    decision: RoutingDecision | None = None
    fallback_outcome: str = "not_used"
    pii_policy: PIIPolicy | None = None
    session_id: str | None = None
    group_id: str | None = None  # the user's usage group, for attribution
    project_id: str | None = None  # the session's chat project, for attribution
    skills: list[ResolvedSkill] = field(default_factory=list)
    #: Skills the user asked for by name (the chat's skill picker).
    requested_skills: list[str] = field(default_factory=list)
    #: Whether the model was offered ``read_skill_file``.
    skill_files: bool = False
    rag_context: dict[str, Any] | None = None
    trace_context: Any = None
    released: bool = False
    #: Whether the knowledge tools were offered to the model.
    knowledge: bool = False
    #: Attached files too long to include, which the model can search.
    attachments: list[attachment_search.SearchableFile] = field(default_factory=list)
    #: The tools the gateway ran, for the audit log, and in how many rounds.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_iterations: int = 0
    #: The MCP tools whose server is set to allow personal data.
    mcp_pii_allowed: set[str] = field(default_factory=set)
    #: The tools the client defined: its own to run.
    client_tools: set[str] = field(default_factory=set)
    #: Whether the system tools were offered (current_time, get_available_tools).
    system_tools: bool = False
    #: The MCP tools the chat may use, in the catalog's order: what
    #: get_available_tools searches.
    mcp_findable: list[dict[str, Any]] = field(default_factory=list)
    #: The MCP tools switched on in the chat: offered, and run when called.
    mcp_on: set[str] = field(default_factory=set)
    #: MCP tools switched on this round, not yet kept with the chat.
    mcp_found: list[str] = field(default_factory=list)
    #: MCP tools offered because the question names them: kept on in the
    #: chat once the model calls them.
    mcp_picked: set[str] = field(default_factory=set)
    #: Whether tools were switched on this round: the next offers them.
    mcp_changed: bool = False
    #: What the model and the tools said to each other, round by round (the
    #: assistant's tool_calls turn, then the tool answers), as the model saw it.
    tool_exchange: list[dict[str, Any]] = field(default_factory=list)
    #: How a streamed answer ended.
    finish_reason: str | None = None
    #: The agent the request runs under (``/v1/agents/{agent}/...``).
    agent: AgentRequest | None = None
    #: Whether the client asked for Faden's events (``X-Faden-Events``).
    faden_events: bool = False
    #: What the egress PII scan found: entity type -> count.
    pii_types: dict[str, int] = field(default_factory=dict)
    #: Faden events not yet sent (tool ends, citations, gateway rounds).
    faden_pending: list[dict[str, Any]] = field(default_factory=list)
    #: Faden events of a whole answer, sent in its ``faden`` object.
    faden_whole: list[dict[str, Any]] = field(default_factory=list)
    #: Faden events sent before the answer (tools the client offered and a rule refuses).
    faden_leading: list[dict[str, Any]] = field(default_factory=list)
    #: The governance checks this request runs: EE's, and the tenant's tool rules.
    checks: list[Any] = field(default_factory=list)
    #: The user's messages this request stored in its session.
    user_message_ids: list[uuid.UUID] = field(default_factory=list)
    #: An output check refused the answer: the decision and the answer's HMAC.
    blocked_output: tuple[uuid.UUID | None, str | None] | None = None
    #: The ``X-Faden-Tool-Mode`` header, for a request without a session setting.
    tool_mode_header: str | None = None
    #: The tool mode this request runs under, and what classifies its tools.
    tool_mode: tool_classes.EffectiveMode | None = None
    classifier: tool_classes.Classifier | None = None
    #: The reply's row in the chat session (a turn), written as it starts.
    turn_id: str | None = None
    #: Writing that row, which runs beside the model call.
    turn_written: asyncio.Future[Any] | None = None

    @property
    def instance_id(self) -> str | None:
        return str(self.decision.candidate.instance_id) if self.decision is not None else None

    @property
    def trace_id(self) -> str | None:
        return self.trace_context.trace_id if self.trace_context is not None else None

    @property
    def fallback_error_code(self) -> str | None:
        if self.fallback_outcome == "fallback_to_local_succeeded":
            return "pii_fallback_to_local_succeeded"
        return None


class GatewayService:
    """Core shared pipeline for chat + embeddings + models."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        router: RouterService,
        proxy: UpstreamProxy,
        adapter: LLMAdapter,
        limiter: RateLimiter,
        audit: AuditService,
        observability: GatewayObservability,
        pii_client: PIIClient | None = None,
        rag_lite_client: RagLiteClient | None = None,
        session_dao: SessionDAO | None = None,
        file_store: FileStore | None = None,
        mcp_client: MCPClient | None = None,
        mcp_tool_cache: MCPToolCache | None = None,
        skills_client: SkillsClient | None = None,
        tool_router: ToolRouter | None = None,
        usage_groups: UsageGroupResolver[str] | None = None,
        group_memberships: UsageGroupResolver[tuple[str, ...]] | None = None,
    ) -> None:
        self.dao = dao
        self.router = router
        self.proxy = proxy
        self.adapter = adapter
        self.limiter = limiter
        self.audit = audit
        self.observability = observability
        self.pii_client = pii_client
        self.rag_lite_client = rag_lite_client
        self.session_dao = session_dao
        self._file_store = file_store
        self.mcp_client = mcp_client
        self.mcp_tool_cache = mcp_tool_cache
        self.skills_client = skills_client
        self.tool_router = tool_router
        self.usage_groups = usage_groups
        self.group_memberships = group_memberships
        self.stream_buffer: StreamBuffer | None = None

    async def list_models(self, auth: AuthContext) -> dict[str, Any]:
        aliases = await self.dao.list_enabled_aliases_for_tenant(auth.tenant_id)
        # What each one is for, so a chat screen offers chat models and a
        # retrieval setting embedding ones. With models of several kinds behind
        # one gateway, a list that did not say offered an embedding model as a
        # chat model, and picking it failed.
        kinds = await self.dao.alias_kinds([a.alias for a in aliases])
        return {
            "object": "list",
            "data": [
                {
                    "id": alias.alias,
                    "alias": alias.alias,
                    "object": "model",
                    "created": int(alias.created_at.timestamp()),
                    "owned_by": "fadenstack",
                    "description": alias.description,
                    "enabled": alias.enabled,
                    "kind": kinds.get(alias.alias),
                }
                for alias in aliases
            ],
        }

    async def route_non_stream(
        self,
        *,
        auth: AuthContext,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str,
        session_id: str | None = None,
        skills: list[str] | None = None,
        agent: AgentRequest | None = None,
        faden_events: bool = False,
        tool_mode: str | None = None,
    ) -> GatewayResponse:
        started = time.perf_counter()
        rerank: dict[str, Any] | None = None
        images: dict[str, Any] | None = None
        if endpoint == "/v1/rerank":
            rerank, payload = payload, _as_input(payload)
        elif endpoint == "/v1/images/generations":
            # The prompt as the pipeline knows texts (``input``): PII, limits and the trace read it there.
            prompt = str(payload.get("prompt") or "")
            images, payload = payload, {"model": payload.get("model"), "input": [prompt]}
        model_alias = _require_model(payload)
        policy = await self.dao.get_tenant_policy(auth.tenant_id)

        req = _Prepared(
            payload=payload, requested_skills=list(skills or []), agent=agent, faden_events=faden_events,
            tool_mode_header=tool_mode,
        )
        result: UpstreamResult | None = None
        error_code: str | None = None
        status_code = 500
        usage_prompt = None
        usage_completion = None
        usage_total = None
        # ── Observability tracking ──────────────────────────────────────
        retry_count = 0
        finish_reason: str | None = None
        try:
            # Inside the try: a request refused here (rate limit, unknown
            # model) gets its audit row like any other; it got none.
            candidates = await self._admit(
                auth=auth, payload=payload, policy=policy, endpoint=endpoint, model_alias=model_alias,
            )
            if rerank is not None:
                candidates = _rerank_routes(model_alias, candidates)
            await self._prepare(
                req,
                auth=auth,
                endpoint=endpoint,
                candidates=candidates,
                request_id=request_id,
                session_id=session_id,
                policy=policy,
                stream=False,
            )
            decision = req.decision
            assert decision is not None and req.egress_payload is not None  # noqa: S101

            for attempt in range(settings.retry_pre_first_token + 1):
                try:
                    if endpoint == "/v1/embeddings":
                        # Embeddings go to the embeddings API. They were sent
                        # through the chat path, which an embedding model
                        # refuses ("does not support Chat Completions API"),
                        # so /v1/embeddings could not reach one at all.
                        adapter_result = await self.adapter.embedding(
                            **_candidate_adapter_kwargs(decision.candidate),
                            payload=req.egress_payload,
                        )
                    elif images is not None:
                        adapter_result = await self.adapter.image_generation(
                            **_candidate_adapter_kwargs(decision.candidate),
                            payload={
                                "model": model_alias,
                                "prompt": req.egress_payload["input"][0],
                                **{k: images[k] for k in _IMAGE_FIELDS if images.get(k) is not None},
                            },
                        )
                    elif rerank is not None:
                        texts = req.egress_payload["input"]
                        query, documents = _in_model_format(decision.candidate, model_alias, texts[0], texts[1:])
                        adapter_result = await self.adapter.rerank(
                            **_candidate_adapter_kwargs(decision.candidate),
                            requested_model=model_alias,
                            query=query,
                            documents=documents,
                            top_n=rerank.get("top_n"),
                        )
                    else:
                        adapter_result = await self.adapter.completion(
                            **_candidate_adapter_kwargs(decision.candidate),
                            payload=req.egress_payload,
                            stream=False,
                        )
                    from faden_gateway.services.gateway.llm_adapter import CompletionResult  # noqa: PLC0415
                    assert isinstance(adapter_result, CompletionResult)  # noqa: S101
                    result = UpstreamResult(
                        status_code=adapter_result.status_code,
                        payload=adapter_result.payload,
                        headers={},
                    )
                    status_code = result.status_code
                    retry_count = attempt
                    break
                except Exception as exc:
                    if attempt >= settings.retry_pre_first_token:
                        raise GatewayError(
                            status_code=502,
                            message=f"Upstream request failed: {exc}",
                            error_type="server_error",
                            code="upstream_request_failed",
                        ) from exc
            if result is None:
                raise GatewayError(
                    status_code=502,
                    message="Upstream returned no response.",
                    error_type="server_error",
                    code="upstream_request_failed",
                )
            # The tools the gateway runs: its own, knowledge, attached files,
            # skill files, MCP, the tool router's.
            if endpoint == "/v1/chat/completions" and (
                req.system_tools or req.knowledge or req.attachments or req.skill_files
                or self.mcp_client or self.tool_router
            ):
                result = await self._tool_loop(req, result, auth=auth, request_id=request_id)

            # The model is done with this request: its slot is free for the
            # next one while the answer is saved and logged.
            await self._release(req)
            if rerank is not None and result.status_code == 200:
                result = UpstreamResult(
                    status_code=200, payload=_reranked(result.payload, rerank, model_alias), headers={},
                )

            # Extract finish_reason from the final result
            _choices = (result.payload or {}).get("choices") or []
            if _choices:
                finish_reason = _choices[0].get("finish_reason")

            usage = usage_from_payload(result.payload)
            usage_prompt = usage.prompt_tokens
            usage_completion = usage.completion_tokens
            usage_total = usage.total_tokens
            latency_ms = int((time.perf_counter() - started) * 1000)

            # The client gets the values tokenize mode took out; the trace
            # keeps the answer as the model gave it, with the tokens. Pictures
            # hold no text to restore, and stay out of the trace.
            response_payload = (
                result.payload if images is not None
                else restore_payload(result.payload, req.token_mapping, req.client_tools)
            )
            if req.faden_events and isinstance(response_payload, dict):
                # Beside the OpenAI fields, which an OpenAI client reads as ever.
                events = [*_pii_events(req), *req.faden_whole]
                response_payload = {**response_payload, "faden": {"events": events}}
            if endpoint == "/v1/chat/completions" and gov.output_window(req.checks):
                response_payload = await self._check_whole_answer(
                    req, response_payload, auth=auth, request_id=request_id,
                )
                finish_reason = "content_filter" if req.blocked_output else finish_reason

            if req.trace_context is not None:
                self.observability.record_success(
                    req.trace_context,
                    status_code=result.status_code,
                    latency_ms=latency_ms,
                    ttft_ms=None,
                    prompt_tokens=usage_prompt,
                    completion_tokens=usage_completion,
                    total_tokens=usage_total,
                    provider_instance_id=req.instance_id,
                    output_payload=_image_trace(result.payload) if images is not None else result.payload,
                )
            # Persist assistant response in session
            await self._persist_assistant_response(
                session_id_str=req.session_id,
                response_payload=response_payload,
                model_alias=model_alias,
                provider_instance_id=req.instance_id,
                trace_id=req.trace_id,
                tool_exchange=req.tool_exchange,
                blocked=req.blocked_output,
            )

            # Best effort, and the client need not wait for it.
            _in_background(self._record_skills_usage(req.skills, auth, session_id=req.session_id))

            return GatewayResponse(
                status_code=result.status_code,
                payload=response_payload,
                provider_instance_id=str(decision.candidate.instance_id),
                latency_ms=latency_ms,
                trace_id=req.trace_id,
            )
        except GatewayError as exc:
            exc.logged = True  # the finally below writes its row
            error_code = exc.code
            status_code = exc.status_code
            if req.trace_context is None:
                req.trace_context = self.observability.start_request_trace(
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    payload={"model": payload.get("model"), "_pii_mode": "pre_upstream_error"},
                    privacy_mode=_privacy_mode(policy, req),
                    stream=False,
                    routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
                )
            self.observability.record_failure(
                req.trace_context,
                status_code=exc.status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                provider_instance_id=req.instance_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        finally:
            await self._release(req)
            if status_code >= 400:
                await self._mark_turn(req, TurnStatus.FAILED.value)
            await self.audit.log(
                request_id=request_id,
                trace_id=req.trace_id,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                model_alias=model_alias,
                provider_instance_id=req.instance_id,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                ttft_ms=None,
                prompt_tokens=usage_prompt,
                completion_tokens=usage_completion,
                total_tokens=usage_total,
                error_code=error_code or req.fallback_error_code,
                stream=False,
                provider_name=(
                    req.decision.candidate.litellm_provider
                    if req.decision is not None else None
                ),
                session_id=req.session_id or session_id,
                group_id=req.group_id,
                project_id=req.project_id,
                finish_reason=finish_reason,
                retry_count=retry_count,
                skills_used=_skills_used(req.skills),
                rag_context=req.rag_context,
                mcp_tool_call_count=len(req.tool_calls),
                mcp_tool_loop_iterations=req.tool_iterations,
                tool_calls=req.tool_calls,
                **_agent_log_fields(req),
            )

    async def route_stream_chat(
        self,
        *,
        auth: AuthContext,
        payload: dict[str, Any],
        request_id: str,
        session_id: str | None = None,
        skills: list[str] | None = None,
        agent: AgentRequest | None = None,
        faden_events: bool = False,
        tool_mode: str | None = None,
        background: bool = False,
    ) -> StreamingGatewayResponse:
        """A streamed chat completion.

        *background* (the chat page): a reply in a chat runs on its own, so
        the page may leave and come back to it; the response only reads it.
        Otherwise the client leaving ends the reply, as it always did.
        """
        started = time.perf_counter()
        endpoint = "/v1/chat/completions"
        model_alias = _require_model(payload)
        policy = await self.dao.get_tenant_policy(auth.tenant_id)

        req = _Prepared(
            payload=payload, requested_skills=list(skills or []), agent=agent, faden_events=faden_events,
            tool_mode_header=tool_mode,
        )
        background = background and bool(session_id) and self.session_dao is not None
        stream_started = False
        stats: StreamStats | None = None
        pre_stream_status_code = 500
        pre_stream_error_code: str | None = None
        pre_stream_error = "The request failed before the model answered."
        try:
            candidates = await self._admit(
                auth=auth, payload=payload, policy=policy, endpoint=endpoint, model_alias=model_alias,
            )
            if background:
                await self._check_turn_limits(auth, str(session_id))
            await self._prepare(
                req,
                auth=auth,
                endpoint=endpoint,
                candidates=candidates,
                request_id=request_id,
                session_id=session_id,
                policy=policy,
                stream=True,
            )
            decision = req.decision
            egress_payload = req.egress_payload
            assert decision is not None and egress_payload is not None  # noqa: S101
            # The reply's row, written beside the model call: whatever becomes
            # of the stream, the chat keeps how the reply ended.
            self._start_turn(req, model_alias=model_alias)
            mcp_tools_injected = any(
                (t.get("function", {}).get("name") or "").startswith(MCP_TOOL_PREFIX)
                for t in (egress_payload.get("tools") or [])
            )

            if (
                req.system_tools or req.knowledge or req.attachments or req.skill_files
                or (mcp_tools_injected and (self.mcp_client or self.tool_router))
            ):
                # Rounds: the answer's text streams as it comes, and the tools
                # run between rounds. With tools, a streamed chat used to be
                # answered whole and then replayed: nothing reached the client
                # until every tool had run and the answer was complete.
                wrapped_stream, stats = await wrap_sse_stream(
                    _leading(_pii_events(req), self._govern_output(
                        req, self._stream_tool_rounds(req, auth=auth, request_id=request_id),
                        auth=auth, request_id=request_id, model_alias=model_alias,
                    )),
                )
            else:
                raw_stream = self.adapter.completion(
                    **_candidate_adapter_kwargs(decision.candidate),
                    payload=egress_payload,
                    stream=True,
                    first_token_timeout_sec=_first_token_wait(decision.candidate),
                )
                # raw_stream is a coroutine returning AsyncIterator[bytes]
                raw_stream = await raw_stream  # type: ignore[misc]
                # The tokens tokenize mode put in go back to the values as the
                # answer streams. They were not: the mapping was dropped here,
                # and the chat page -- which always streams -- showed
                # "Hello [PERSON_1]".
                wrapped_stream, stats = await wrap_sse_stream(
                    _leading(_pii_events(req), self._govern_output(
                        req, restore_sse(raw_stream, req.token_mapping, req.client_tools),
                        auth=auth, request_id=request_id, model_alias=model_alias,
                    )),
                )
            stream_started = True

            # Start stream buffer for SSE reconnection
            _sbuf = self.stream_buffer
            _sbuf_sid = req.session_id
            if _sbuf and _sbuf_sid:
                _sbuf.start(_sbuf_sid, trace_id=req.trace_id, turn_id=req.turn_id)

            def _finish(
                content: str, status_code: int, error_code: str | None, closing: bool, saved: asyncio.Event,
                *, interrupted: bool = False,
            ) -> Awaitable[None]:
                return self._finish_stream(
                    req,
                    auth=auth,
                    request_id=request_id,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    session_id=session_id,
                    started=started,
                    stats=stats,
                    content=content,
                    status_code=status_code,
                    error_code=error_code,
                    closing=closing,
                    stream_buffer=_sbuf,
                    buffer_session_id=_sbuf_sid,
                    saved=saved,
                    interrupted=interrupted,
                )

            if background and req.turn_id and _sbuf and _sbuf_sid:
                self._own_session()
                _kept(self._run_turn(req, wrapped_stream, stats, buffer=_sbuf, finish=_finish))

                async def _follow() -> AsyncIterator[bytes]:
                    # The reply as any page that comes back reads it.
                    async for chunk in await _sbuf.subscribe(_sbuf_sid):
                        yield chunk

                return StreamingGatewayResponse(
                    stream=_follow(),
                    provider_instance_id=str(decision.candidate.instance_id),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    stats=stats,
                    trace_id=req.trace_id,
                    turn_id=req.turn_id,
                )

            async def _stream_with_finalize() -> AsyncIterator[bytes]:
                stream_status_code = 200
                stream_error_code: str | None = None
                accumulated_content: list[str] = []
                closing = False
                failure: bytes | None = None
                try:
                    async for chunk in wrapped_stream:
                        # The closing [DONE] is held back until the answer is
                        # saved and the request logged. A client that stops at
                        # [DONE] and closes -- as the OpenAI SDKs do -- had
                        # Starlette cancel that work half way: the reply and
                        # the usage row were lost for every such request.
                        piece, seen = _split_done(chunk)
                        closing = closing or seen
                        if not piece:
                            continue
                        # Collect assistant content for persistence
                        _accumulate_stream_content(piece, accumulated_content)
                        # Push to reconnection buffer
                        if _sbuf and _sbuf_sid:
                            _sbuf.push(_sbuf_sid, piece)
                        yield piece
                except Exception as exc:
                    stream_status_code = 502
                    stream_error_code = "upstream_stream_failed"
                    # The response has started, so no HTTP error can say it
                    # any more: an error event does, before [DONE]. Without it
                    # a cut-off answer looked like a whole one.
                    failure = _stream_error_event(exc)
                    if isinstance(exc, GatewayError) and exc.code:
                        stream_error_code = exc.code
                    del exc
                    closing = True
                finally:
                    stream_status_code, stream_error_code = _stream_outcome(
                        stats, stream_status_code, stream_error_code,
                    )
                    # Finished even when the client has gone: see _to_the_end.
                    # The closing [DONE] waits only for the saved reply, which a
                    # client can see (the chat page reloads the session there);
                    # the audit row and the trace follow on their own.
                    saved = asyncio.Event()
                    await _to_the_end(_finish(
                        "".join(accumulated_content), stream_status_code, stream_error_code, closing, saved,
                    ), until=saved)
                if failure is not None:
                    yield failure
                if closing:
                    yield _DONE_EVENT

            return StreamingGatewayResponse(
                stream=_stream_with_finalize(),
                provider_instance_id=str(decision.candidate.instance_id),
                latency_ms=int((time.perf_counter() - started) * 1000),
                stats=stats,
                trace_id=req.trace_id,
                turn_id=req.turn_id,
            )
        except GatewayError as exc:
            exc.logged = True  # the finally below writes its row
            pre_stream_status_code = exc.status_code
            pre_stream_error_code = exc.code
            pre_stream_error = exc.message
            if req.trace_context is None:
                req.trace_context = self.observability.start_request_trace(
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    payload={"model": payload.get("model"), "_pii_mode": "pre_upstream_error"},
                    privacy_mode=_privacy_mode(policy, req),
                    stream=True,
                    routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
                )
            self.observability.record_failure(
                req.trace_context,
                status_code=exc.status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                provider_instance_id=req.instance_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        finally:
            if not stream_started:
                await self._release(req)
                # The question was saved before this failed: the chat keeps the
                # failure with it, to be seen and tried again after a reload.
                if req.session_id:
                    try:
                        await self._persist_stream_assistant_response(
                            session_id_str=req.session_id,
                            content="",
                            model_alias=model_alias,
                            trace_id=req.trace_id,
                            status=TurnStatus.FAILED.value,
                            error=pre_stream_error,
                            turn=req,
                        )
                    except Exception:
                        logger.warning("Failed to keep the failed reply", exc_info=True)
                await self.audit.log(
                    request_id=request_id,
                    trace_id=req.trace_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    model_alias=model_alias,
                    provider_instance_id=req.instance_id,
                    endpoint=endpoint,
                    status_code=pre_stream_status_code,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    ttft_ms=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                    error_code=pre_stream_error_code or req.fallback_error_code,
                    stream=True,
                    provider_name=(
                        req.decision.candidate.litellm_provider
                        if req.decision is not None else None
                    ),
                    session_id=session_id,
                    group_id=req.group_id,
                    project_id=req.project_id,
                    finish_reason=None,
                    retry_count=0,
                    skills_used=_skills_used(req.skills),
                    rag_context=req.rag_context,
                    mcp_tool_call_count=0,
                    mcp_tool_loop_iterations=0,
                    tool_calls=[],
                    **_agent_log_fields(req),
                )

    # ------------------------------------------------------------------
    # On the way to the model
    # ------------------------------------------------------------------

    async def _admit(
        self, *, auth: AuthContext, payload: dict[str, Any], policy: Any | None, endpoint: str, model_alias: str,
    ) -> list[Any]:
        """Rate limits, then the routes for the model; raises when the request cannot go on."""
        await _check_limits(
            limiter=self.limiter,
            tenant_id=auth.tenant_id,
            payload=payload,
            rpm_limit=policy.rpm_limit if policy else None,
            tpm_limit=policy.tpm_limit if policy else None,
        )
        candidates = await self.router.resolve_alias(alias=model_alias, tenant_id=auth.tenant_id)
        _check_kind(endpoint, model_alias, candidates)
        return candidates

    # ------------------------------------------------------------------
    # Governance (services/gateway/governance.py)
    # ------------------------------------------------------------------

    def _context(
        self, req: _Prepared, auth: AuthContext, request_id: str, stage: gov.GovernanceStage,
        session_id: str | None = None, **fields: Any,
    ) -> gov.GovernanceContext:
        return gov.GovernanceContext(
            stage=stage,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            agent_id=req.agent.agent.id if req.agent is not None else None,
            session_id=session_id or req.session_id,
            request_id=request_id,
            **fields,
        )

    async def _resolve_tool_mode(
        self, req: _Prepared, auth: AuthContext, mcp_tools: list[dict[str, Any]] | None,
    ) -> None:
        """The chat's tool mode (narrowed by any ceiling), what classifies its tools, and its check.

        The session's setting when it has one, else the ``X-Faden-Tool-Mode``
        header, else Ask; both given, the narrower. EE ceilings narrow it
        further (``registry.tool_mode_ceilings``).
        """
        header = tool_classes.parse_mode(req.tool_mode_header)
        chosen = header
        sid = _uuid_or_none(req.session_id)
        if sid is not None:
            policy = await self.dao.get_session_execution_policy(sid)
            stored = tool_classes.parse_mode(policy.tool_mode if policy else None)
            if stored is not None:
                chosen = tool_classes.narrowest(stored, header) if header else stored
        groups: tuple[str, ...] = ()
        if extension_registry.tool_mode_ceilings and self.group_memberships is not None:
            groups = await self.group_memberships.resolve(auth.user_id) or ()
        req.tool_mode = await tool_classes.effective(
            chosen, extension_registry.tool_mode_ceilings,
            tool_classes.CeilingContext(
                tenant_id=auth.tenant_id, user_id=auth.user_id,
                agent_id=req.agent.agent.id if req.agent is not None else None,
                session_id=req.session_id, groups=tuple(groups),
            ),
        )
        contract = (
            {str(t.get("name", "")): str(t.get("risk") or "") for t in req.agent.agent.host_tool_contract}
            if req.agent is not None else None
        )
        req.classifier = tool_classes.Classifier(
            rules=await tool_classes.class_rules.for_tenant(self.dao.session, auth.tenant_id),
            gateway_tools=frozenset(_GATEWAY_TOOLS),
            contract=contract,
            annotations={
                t.get("qualified_name", ""): t["annotations"] for t in mcp_tools or [] if t.get("annotations")
            },
        )
        req.checks.append(ToolModeCheck(req.classifier, req.tool_mode.mode))

    def _hidden(self, req: _Prepared, name: str) -> bool:
        if req.tool_mode is None or req.classifier is None:
            return False
        decision = tool_classes.decide(req.tool_mode.mode, req.classifier.classify(name))
        return decision is tool_classes.Decision.HIDDEN

    def _offer_by_mode(self, req: _Prepared, egress: dict[str, Any]) -> dict[str, Any]:
        """The tools the mode lets the model see; the others are not offered at all."""
        tools = egress.get("tools") or []
        kept = [t for t in tools if not self._hidden(req, _tool_definition_name(t))]
        hidden = [_tool_definition_name(t) for t in tools if t not in kept]
        if req.faden_events and req.tool_mode is not None:
            req.faden_leading.append({"type": "CUSTOM", "name": "faden.tool_mode", "value": {
                **req.tool_mode.as_dict(), "hidden": hidden,
            }})
        if not hidden:
            return egress
        for name in hidden:
            req.client_tools.discard(name)
        out = {k: v for k, v in egress.items() if k != "tools"}
        if kept:
            out["tools"] = kept
        choice = out.get("tool_choice")
        if not kept or (isinstance(choice, dict) and (choice.get("function") or {}).get("name") in hidden):
            out.pop("tool_choice", None)
        return out

    async def _governance_checks(self, auth: AuthContext) -> list[Any]:
        """EE's checks and the tenant's tool rules; empty in a plain core install without rules."""
        checks: list[Any] = list(extension_registry.governance_checks)
        rules = await gov.tool_rules.for_tenant(self.dao.session, auth.tenant_id)
        if rules:
            checks.append(gov.ToolRuleCheck(rules))
        return checks

    async def _check_input(
        self, req: _Prepared, *, auth: AuthContext, session_id: str | None, request_id: str,
    ) -> None:
        """Refuse the prompt before anything is stored; the turn stays as a notice, not text."""
        messages = req.payload.get("messages") or []
        ctx = self._context(req, auth, request_id, gov.GovernanceStage.INPUT, session_id, messages=messages)
        verdict = await gov.decide(req.checks, ctx)
        if verdict is None:
            return
        decision_id, digest = await gov.record(
            self.dao.session, ctx, verdict, text=gov.text_of(messages), outcome="block",
        )
        stored_session: str | None = None
        message_id: uuid.UUID | None = None
        sid = _uuid_or_none(session_id)
        if sid is not None and self.session_dao is not None:
            owned = await self.session_dao.get_session(session_id=sid, tenant_id=auth.tenant_id, user_id=auth.user_id)
            if owned is not None:
                stored = await self.session_dao.append_message(
                    session_id=sid, role="user", content="", status=TurnStatus.BLOCKED_INPUT.value,
                    decision_id=decision_id, content_hmac=digest,
                )
                stored_session, message_id = str(sid), stored.id
        await self.dao.session.commit()
        raise gov.prompt_blocked(gov.governance_object(
            verdict, outcome="prompt_blocked", stage=gov.GovernanceStage.INPUT, decision_id=decision_id,
            request_id=request_id, session_id=stored_session, message_id=message_id,
        ))

    async def _screen_client_tools(self, req: _Prepared, *, auth: AuthContext, request_id: str) -> None:
        """The client's tools a rule refuses are not offered to the model.

        The client runs its own tools, so refusing a call would come too late:
        the model never sees the tool. One that needs approval stays: the
        client asks its user.
        """
        if not any(gov.GovernanceStage.TOOL in c.stages for c in req.checks):
            return
        tools = req.payload.get("tools") or []
        kept = []
        for tool in tools:
            name = _tool_definition_name(tool)
            ctx = self._context(req, auth, request_id, gov.GovernanceStage.TOOL, tool_id=name)
            verdict = await gov.decide(req.checks, ctx)
            if verdict is None or not verdict.blocked:
                kept.append(tool)
                continue
            decision_id, _ = await gov.record(self.dao.session, ctx, verdict, text=None)
            req.client_tools.discard(name)
            req.faden_leading.append(gov.tool_denied_event({}, name, verdict.decision.message, decision_id))
        if len(kept) == len(tools):
            return
        payload = {k: v for k, v in req.payload.items() if k != "tools"}
        if kept:
            payload["tools"] = kept
        choice = payload.get("tool_choice")
        if not kept or (isinstance(choice, dict) and (choice.get("function") or {}).get("name") not in req.client_tools):
            payload.pop("tool_choice", None)
        req.payload = payload

    async def _screen_calls(
        self, req: _Prepared, calls: list[dict[str, Any]], *, auth: AuthContext, request_id: str,
    ) -> dict[str, tuple[str, gov.Verdict]]:
        """The gateway's calls a check objects to: call id -> (tool, verdict)."""
        if not any(gov.GovernanceStage.TOOL in c.stages for c in req.checks):
            return {}
        found: dict[str, tuple[str, gov.Verdict]] = {}
        for call in calls:
            name = _mcp_name(req, _tool_name(call))
            ctx = self._context(
                req, auth, request_id, gov.GovernanceStage.TOOL, tool_id=name, arguments=_tool_arguments(call),
            )
            verdict = await gov.decide(req.checks, ctx)
            if verdict is not None:
                found[call.get("id", "")] = (name, verdict)
        return found

    async def _ask_approvals(
        self, req: _Prepared, calls: list[dict[str, Any]], screened: dict[str, tuple[str, gov.Verdict]],
        answered: dict[str, bool], *, auth: AuthContext, request_id: str,
    ) -> AsyncIterator[bytes]:
        """Ask the user about each call that needs approval, one at a time; answers go in *answered*.

        Only a stream to a client that reads events can ask; anywhere else the
        call is refused, saying it needs approval.
        """
        if not req.faden_events:
            return
        timeout = float(settings.governance_approval_timeout_s)
        for call in calls:
            call_id = call.get("id", "")
            name, verdict = screened.get(call_id, ("", None))  # type: ignore[assignment]
            if verdict is None or verdict.blocked:
                continue
            approval_id = uuid.uuid4().hex
            yield faden_events.sse_event(gov.approval_event(approval_id, call, name, verdict.decision.message, timeout))
            approved = await gov.approvals.wait(
                tenant_id=auth.tenant_id, user_id=auth.user_id, approval_id=approval_id, timeout=timeout,
            )
            answered[call_id] = bool(approved)
            ctx = self._context(req, auth, request_id, gov.GovernanceStage.TOOL, tool_id=name)
            outcome = "approved" if approved else ("rejected" if approved is False else "expired")
            await gov.record_on_own_session(self.dao.session.bind, ctx, verdict, text=None, outcome=outcome)

    async def _refused(
        self, req: _Prepared, call: dict[str, Any], name: str, verdict: gov.Verdict, answer: bool | None,
        *, auth: AuthContext, request_id: str,
    ) -> str:
        """Record a refused call and tell the client; what the model is told."""
        message = verdict.decision.message
        if not verdict.blocked:
            message = (
                f"{message} The user declined." if answer is False
                else f"{message} It needs the user's approval, which could not be asked for here."
            )
        ctx = self._context(req, auth, request_id, gov.GovernanceStage.TOOL, tool_id=name)
        decision_id = None
        if verdict.blocked or answer is None:
            decision_id, _ = await gov.record_on_own_session(self.dao.session.bind, ctx, verdict, text=None)
        event = gov.tool_denied_event(call, name, message, decision_id)
        if req.faden_events:
            req.faden_pending.append(event)
        return gov.denied_result(name, message)

    def _govern_output(
        self, req: _Prepared, stream: AsyncIterator[bytes], *, auth: AuthContext, request_id: str, model_alias: str,
    ) -> AsyncIterator[bytes]:
        """*stream*, checked before it is forwarded -- unchanged without a check that reads answers."""
        window = gov.output_window(req.checks)
        if not window:
            return stream

        async def check(text: str) -> gov.Verdict | None:
            ctx = self._context(req, auth, request_id, gov.GovernanceStage.OUTPUT, text=text)
            return await gov.decide(req.checks, ctx)

        async def on_block(verdict: gov.Verdict, text: str) -> AsyncIterator[bytes]:
            ctx = self._context(req, auth, request_id, gov.GovernanceStage.OUTPUT)
            decision_id, digest = await gov.record_on_own_session(
                self.dao.session.bind, ctx, verdict, text=text, outcome="block",
            )
            req.blocked_output = (decision_id, digest)
            req.finish_reason = "content_filter"
            found = gov.governance_object(
                verdict, outcome="response_blocked", stage=gov.GovernanceStage.OUTPUT, decision_id=decision_id,
                request_id=request_id, session_id=req.session_id,
            )
            if req.faden_events:
                yield faden_events.sse_event(gov.governance_event(found))
            yield gov.content_filter_chunk({"id": f"chatcmpl-{request_id}", "model": model_alias})
            yield _DONE_EVENT

        return gov.govern_sse(stream, window=window, check=check, on_block=on_block)

    async def _check_whole_answer(
        self, req: _Prepared, payload: Any, *, auth: AuthContext, request_id: str,
    ) -> Any:
        """A whole answer an output check refuses: empty, ``content_filter``, ``faden.governance``."""
        choices = (payload or {}).get("choices") or [] if isinstance(payload, dict) else []
        message = choices[0].get("message") or {} if choices else {}
        text = message.get("content")
        if not isinstance(text, str) or not text:
            return payload
        ctx = self._context(req, auth, request_id, gov.GovernanceStage.OUTPUT, text=text)
        verdict = await gov.decide(req.checks, ctx)
        if verdict is None or not verdict.blocked:
            return payload
        decision_id, digest = await gov.record_on_own_session(
            self.dao.session.bind, ctx, verdict, text=text, outcome="block",
        )
        req.blocked_output = (decision_id, digest)
        found = gov.governance_object(
            verdict, outcome="response_blocked", stage=gov.GovernanceStage.OUTPUT, decision_id=decision_id,
            request_id=request_id, session_id=req.session_id,
        )
        blocked = {**choices[0], "message": {"role": "assistant", "content": ""}, "finish_reason": "content_filter"}
        return {**payload, "choices": [blocked, *choices[1:]], "faden": {**(payload.get("faden") or {}), "governance": found}}

    async def _mark_turn(self, req: _Prepared, status: str) -> None:
        """Set the status of the user's messages this request stored (``failed``: left out of context)."""
        if not req.user_message_ids or self.session_dao is None:
            return
        from sqlalchemy import update  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

        try:
            async with AsyncSession(self.dao.session.bind) as own:
                await own.execute(
                    update(ChatMessage).where(ChatMessage.id.in_(req.user_message_ids)).values(status=status),
                )
                await own.commit()
        except Exception:
            logger.warning("Could not mark the turn %s", status, exc_info=True)

    async def _prepare(
        self,
        req: _Prepared,
        *,
        auth: AuthContext,
        endpoint: str,
        candidates: list[Any],
        request_id: str,
        session_id: str | None,
        policy: Any | None,
        stream: bool,
    ) -> None:
        """Gather the context, pass it through PII, then take a model slot.

        One path for streamed and whole answers alike; they had a copy each,
        and the streaming copy had dropped the PII token mapping.

        The slot is taken last. It used to be taken first and held while RAG,
        the session and the PII scans ran -- close to a second with PII on --
        during which the model could have been answering someone else.

        The session, skills and the MCP tool list do not depend on one
        another, so they are looked up side by side: the request waits for the
        slowest of them, not for their sum.
        """
        chat = endpoint == "/v1/chat/completions"
        payload = req.payload
        req.client_tools = {_tool_definition_name(t) for t in payload.get("tools") or []}
        if "rag" in payload:
            # Retrieval before the model, on every turn that asked for it, is
            # gone: the model searches when it needs to (knowledge_search).
            raise GatewayError(
                status_code=400,
                message=(
                    "The rag field is no longer supported: models search the "
                    "knowledge base themselves, with the knowledge_search tool."
                ),
                error_type="invalid_request_error",
                code="unsupported_parameter",
                param="rag",
            )
        if chat and media_fetch.needed(payload.get("messages")):
            # The media URLs the client sent, checked and fetched by the gateway before anything is
            # stored or sent: the model gets them as data: URLs (inlined below), and nothing after the
            # gateway fetches a URL a client named. The session step adds the chat's own pictures
            # after this, as data: URLs, which are never fetched. A fetch can take seconds: the
            # request holds no pooled database connection meanwhile.
            await self._release_request_connection()
            req.media = await media_fetch.fetch_messages(payload.get("messages"))
        # Read before anything runs: the session step rewrites the messages.
        question = _last_user_text(payload)
        if chat:
            # Before anything is stored or sent: a refused prompt leaves no text.
            req.checks = await self._governance_checks(auth)
            if req.checks:
                await self._check_input(req, auth=auth, session_id=session_id, request_id=request_id)
                await self._screen_client_tools(req, auth=auth, request_id=request_id)
                payload = req.payload

        # A file too long to include is searched by the model, if every route
        # the request may take calls tools; told apart before the slot.
        searchable = chat and payload.get("tool_choice") != "none" and all(
            knowledge.calls_tools(c) for c in candidates
        )
        # The files the chat has, for skills that come in on a file type. Read
        # before the lookups below: the session step uses the same database
        # session, which cannot run two queries at once.
        file_types = await self._chat_file_types(auth, session_id) if chat and self.skills_client else []
        (payload, req.session_id, req.project_id), req.skills, mcp_tools, req.group_id = await _together(
            self._inject_session_context(
                payload, auth, session_id, req=req if searchable else None, saved=req.user_message_ids,
            )
            if chat else _value((payload, None, None)),
            self._resolve_skills(
                question,
                auth,
                session_id,
                req.requested_skills,
                file_types=file_types,
                # A model that cannot call read_skill_file gets the files inline.
                inline_files_chars=0 if searchable else skill_files.INLINE_CHARS,
            )
            if chat
            else _value([]),
            self.mcp_tool_cache.get_tools(auth.tenant_id) if chat and self.mcp_tool_cache else _value(None),
            self.usage_groups.resolve(auth.user_id) if self.usage_groups else _value(None),
        )
        insert_skill = (
            _insert_before_question if settings.session_skills_position == "tail" else _insert_after_system
        )
        for skill in req.skills:  # each after the last: they keep their order
            payload = insert_skill(payload, {
                "role": "system",
                "content": skill_files.skill_block(skill, as_tool=searchable),
            })
        req.payload = payload
        if chat:
            # The session is resolved (and the user's): its tool mode applies.
            await self._resolve_tool_mode(req, auth, mcp_tools)
        if mcp_tools:
            req.mcp_findable, req.mcp_on = await self._chat_tools(mcp_tools, auth, req.session_id, req.skills)
            # A tool the mode hides is not found by the tool search either.
            req.mcp_findable = [
                t for t in req.mcp_findable if not self._hidden(req, t.get("qualified_name", ""))
            ]
            req.mcp_on = {n for n in req.mcp_on if not self._hidden(req, n)}
            picked = {t.get("qualified_name", "") for t in system_tools.picked_for(req.mcp_findable, question)}
            req.mcp_picked = picked - req.mcp_on
            req.mcp_on |= req.mcp_picked
        req.mcp_pii_allowed = _allowing_personal_data(req.mcp_findable)

        req.pii_policy = _resolve_pii_policy(
            policy,
            session_override=(
                await self._session_pii_override(req.session_id, auth) or _agent_pii_override(req)
            ),
            agent_allow_mode_override=(
                req.agent.agent.pii_allow_mode_override if req.agent is not None else None
            ),
        )
        scanned, candidates = await self._scan_egress(req, candidates=candidates, request_id=request_id)
        telemetry_payload = None
        if req.pii_policy and self.pii_client and req.pii_policy.telemetry.enabled:
            if scanned is not None and req.pii_policy.telemetry.mode != "metrics_only":
                # One scan serves both what leaves and what the trace keeps:
                # the same text was scanned a second time for the trace, which
                # doubled what PII cost every request.
                telemetry_payload = redact_tokens(*scanned)
            else:
                telemetry_payload = await self._apply_telemetry_pii(
                    payload=payload,
                    pii_policy=req.pii_policy,
                    request_id=request_id,
                )

        # Everything that waits on another service is done: now the slot.
        req.decision = await self._lease(req, candidates=candidates, request_id=request_id)

        egress = payload
        if scanned is not None and _needs_scan(req.pii_policy, req.decision.candidate):
            egress, req.token_mapping = scanned
            egress = self._inject_pii_system_prompt(egress, req.pii_policy)  # type: ignore[arg-type]

        req.trace_context = self.observability.start_request_trace(
            request_id=request_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            endpoint=endpoint,
            model_alias=_require_model(payload),
            payload=telemetry_payload if telemetry_payload is not None else egress,
            privacy_mode=_privacy_mode(policy, req),
            stream=stream,
            routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
        )

        if chat:
            # Inject current date/time so the model is aware of "today"
            egress = self._inject_datetime_context(egress)
            # Tools only to a route that calls them, when the client has not
            # ruled them out: vLLM refuses a request offering tools otherwise.
            if self._offers_system_tools(req.decision.candidate, payload):
                egress = _with_tools(egress, system_tools.tools(find=bool(req.mcp_findable)))
                req.system_tools = True
                if req.mcp_on:
                    egress = self._merge_mcp_tools(egress, _switched_on(req), auth.tenant_id)
            else:
                req.mcp_on = set()
            if self._offers_knowledge(req.decision.candidate, payload):
                egress = _with_tools(egress, knowledge.TOOLS)
                req.knowledge = True
            if req.attachments:
                egress = _with_tools(egress, attachment_search.TOOLS)
        # Apply skill-based tool constraints (after all tools are merged)
        if req.skills:
            egress = self._apply_skill_tool_constraints(egress, req.skills)
        if req.agent is not None:
            egress = _agent_tools(egress, req)
        # A skill's own files are always readable: offered after the
        # constraints, which would otherwise take the tool away again.
        if chat and searchable and skill_files.has_files(req.skills):
            egress = _with_tools(egress, skill_files.TOOLS)
            req.skill_files = True
        egress = _without_replayed_tools(egress)
        if chat:
            egress = self._offer_by_mode(req, egress)
            # Last, on what leaves: every media URL as the data: URL the gateway fetched for it. The
            # tool rounds, the retries and the background turn all send this payload on.
            egress = await media_fetch.inline(egress, req.media)
        req.egress_payload = egress
        # The model call is next, and it lasts seconds.
        await self._release_request_connection()

    async def _release_request_connection(self) -> None:
        """End the request session's transaction, so it holds no pooled connection.

        What the request read or wrote so far is committed here rather than by
        ``get_db_session`` after the response. A session left in its
        transaction kept a connection "idle in transaction" for the whole model
        call: at 64 concurrent streams the gateway held 59 connections, and
        Postgres (100) refused every other service. Later reads and writes
        open a new transaction only for as long as they need it.
        """
        session = self.dao.session
        if session.in_transaction():
            await session.commit()

    async def _release(self, req: _Prepared) -> None:
        """Give the request's model slot back, once."""
        if req.decision is not None and not req.released:
            req.released = True
            await self.router.release(req.decision)

    async def _session_pii_override(self, session_id: str | None, auth: AuthContext) -> Any | None:
        """The PII settings a user chose for this chat session, if any."""
        if not session_id:
            return None
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            return None
        return await self.dao.get_session_pii_override(sid, auth.tenant_id, auth.user_id)

    async def _scan_egress(
        self,
        req: _Prepared,
        *,
        candidates: list[Any],
        request_id: str,
    ) -> tuple[tuple[dict[str, Any], dict[str, str] | None] | None, list[Any]]:
        """Scan what will leave, when any route it may take calls for it.

        Returns the scan (payload and token mapping), or ``None`` when there is
        none, and the candidates left: only the local ones when a failed scan
        falls back to them. The route is chosen after this -- by the slot -- so
        the scan is made if any candidate needs it, and a route that does not
        gets the payload as it was.
        """
        pii_policy = req.pii_policy
        if (
            pii_policy is None
            or self.pii_client is None
            or not any(_needs_scan(pii_policy, c) for c in candidates)
        ):
            return None, candidates
        try:
            return await self._sanitize(req.payload, pii_policy, req), candidates
        except Exception:
            fail_action = pii_policy.egress.fail_action
            if fail_action == "block":
                raise GatewayError(
                    status_code=502,
                    message="PII service unavailable and fail_action=block.",
                    error_type="server_error",
                    code="pii_service_unavailable",
                ) from None
            if fail_action == "fallback_to_local" and any(_is_cloud(c) for c in candidates):
                req.fallback_outcome = "fallback_to_local_attempted"
                local = [c for c in candidates if not _is_cloud(c)]
                if not local:
                    req.fallback_outcome = "fallback_to_local_failed"
                    raise GatewayError(
                        status_code=503,
                        message="PII fallback requested but no local provider candidate is available.",
                        error_type="server_error",
                        code="pii_fallback_no_local_provider",
                    ) from None
                if any(_needs_scan(pii_policy, c) for c in local):
                    try:
                        return await self._sanitize(req.payload, pii_policy, req), local
                    except Exception:
                        # Local is let through, as it was before.
                        logger.warning(
                            "PII egress scan failed again for %s; sending to local unscanned", request_id,
                        )
                return None, local
            logger.warning(
                "PII egress scan failed for %s; fail_action=%s, allowing through",
                request_id,
                fail_action,
            )
            return None, candidates

    async def _sanitize(
        self, payload: dict[str, Any], pii_policy: PIIPolicy, req: _Prepared | None = None,
    ) -> tuple[dict[str, Any], dict[str, str] | None]:
        assert self.pii_client is not None  # noqa: S101
        result = await self.pii_client.sanitize(
            payload=payload,
            policy=pii_policy,
            mode=pii_policy.egress.mode,
        )
        if req is not None:
            # Types and counts only, for a notice in the UI -- never the values.
            for kind, count in result.entity_types.items():
                req.pii_types[kind] = req.pii_types.get(kind, 0) + count
        return result.sanitized_payload, result.token_mapping

    async def _lease(
        self, req: _Prepared, *, candidates: list[Any], request_id: str,
    ) -> RoutingDecision:
        """Take a model slot among *candidates*."""
        if req.fallback_outcome != "fallback_to_local_attempted":
            return await self.router.pick_and_lease(candidates=candidates, request_id=request_id)
        try:
            decision = await self.router.pick_and_lease(candidates=candidates, request_id=request_id)
        except GatewayError as exc:
            req.fallback_outcome = "fallback_to_local_failed"
            if exc.code == "no_capacity":
                raise GatewayError(
                    status_code=503,
                    message="PII fallback requested but no local provider has free capacity.",
                    error_type="server_error",
                    code="pii_fallback_no_local_capacity",
                ) from exc
            raise
        req.fallback_outcome = "fallback_to_local_succeeded"
        return decision

    # ------------------------------------------------------------------
    # PII helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mcp_pii_mode_override(
        pii_policy: PIIPolicy | None,
        decision: RoutingDecision | None,
    ) -> str | None:
        """Compute PII mode override for MCP tool calls.

        When the egress PII policy disables scanning for the current
        provider type (e.g. ``enabled_for_local=false`` and the provider
        is local), returns ``"allow"`` so the MCP proxy skips PII
        scanning.  Otherwise returns ``None`` (use server default).
        """
        if pii_policy is None or decision is None:
            return None
        is_cloud = decision.candidate.provider_type.value.startswith("remote_")
        should_scan = (
            (is_cloud and pii_policy.egress.enabled_for_cloud)
            or (not is_cloud and pii_policy.egress.enabled_for_local)
        )
        if not should_scan:
            return "allow"
        return None

    @staticmethod
    def _inject_datetime_context(
        egress_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Prepend a system message with the current date.

        Ensures the model knows "today's" date so it can reason about
        recency, scheduled events, etc.  Inserted as the very first
        system message so it doesn't displace user-provided prompts.

        The date, not the time: it is the first thing the model reads, and
        vLLM reuses its cache only for an identical prefix. With the minute
        in it, every turn a minute after the last recomputed the whole
        history -- 2 to 3 times the time to the first token at 4k to 10k
        tokens on qwen-chat (2026-09-25).
        """
        messages = egress_payload.get("messages")
        if not isinstance(messages, list):
            return egress_payload

        now = datetime.now(timezone.utc)
        # %-d (no zero-pad) is a GNU/POSIX extension that raises
        # ValueError on Windows; zero-padded %d works everywhere.
        date_str = now.strftime("%A, %B %d, %Y")
        prompt = f"Current date: {date_str}."
        date_msg: dict[str, str] = {"role": "system", "content": prompt}

        new_messages = [date_msg, *messages]
        return {**egress_payload, "messages": new_messages}

    @staticmethod
    def _inject_pii_system_prompt(
        egress_payload: dict[str, Any],
        pii_policy: PIIPolicy,
    ) -> dict[str, Any]:
        """Prepend a system message explaining PII redaction/tokenization.

        Only modifies ``messages``-based payloads (chat completions).
        Returns a shallow-copied payload with the injected message.
        """
        messages = egress_payload.get("messages")
        if not isinstance(messages, list):
            return egress_payload

        prompt = (
            _PII_TOKENIZE_SYSTEM_PROMPT
            if pii_policy.egress.mode == "tokenize_reversible"
            else _PII_REDACT_SYSTEM_PROMPT
        )

        pii_system_msg: dict[str, str] = {"role": "system", "content": prompt}
        # Insert after any existing leading system messages so we don't
        # displace the user's own system prompt.
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        new_messages = list(messages)
        new_messages.insert(insert_idx, pii_system_msg)
        return {**egress_payload, "messages": new_messages}

    async def _apply_telemetry_pii(
        self,
        *,
        payload: dict[str, Any],
        pii_policy: PIIPolicy,
        request_id: str,
    ) -> dict[str, Any]:
        """Produce a PII-clean version of *payload* for observability.

        If the telemetry mode is ``metrics_only`` we return a minimal
        stub so that Langfuse still gets token counts but no text.
        """
        assert self.pii_client is not None  # noqa: S101

        if pii_policy.telemetry.mode == "metrics_only":
            # Strip all text content; keep only model + metadata
            return {"model": payload.get("model"), "_pii_mode": "metrics_only"}

        try:
            result = await self.pii_client.sanitize(
                payload=payload,
                policy=pii_policy,
                mode="redact",  # always redact for telemetry
            )
            return result.sanitized_payload
        except Exception:
            logger.warning(
                "PII telemetry scan failed for %s; falling back to metadata-only",
                request_id,
            )
            return {"model": payload.get("model"), "_pii_mode": "fallback"}

    async def _inject_session_context(
        self,
        payload: dict[str, Any],
        auth: AuthContext,
        session_id_str: str | None,
        req: _Prepared | None = None,
        saved: list[uuid.UUID] | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Inject session history and memory into the payload.

        Returns ``(updated_payload, resolved_session_id_hex)``
        where the session id is ``None`` when sessions are disabled.
        With *req*, attached files too long to include are left for the model
        to search, and go on ``req.attachments``.
        """
        if not session_id_str or not self.session_dao:
            return payload, None, None

        import uuid as _uuid  # noqa: PLC0415

        from faden_gateway.services.gateway.context_assembler import ContextAssembler, in_context  # noqa: PLC0415

        try:
            sid = _uuid.UUID(session_id_str)
        except ValueError:
            return payload, None, None

        sess = await self.session_dao.get_session(
            session_id=sid, tenant_id=auth.tenant_id, user_id=auth.user_id,
        )
        if not sess:
            return payload, None, None

        # Resolve project if the session belongs to one
        project = None
        if sess.project_id:
            project = await self.session_dao.get_project(
                project_id=sess.project_id,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
            )

        # Resolve file store for attachment context injection
        file_store = getattr(self, "_file_store", None)

        assembler = ContextAssembler(
            dao=self.session_dao,
            max_recent_messages=settings.session_max_recent_messages,
            token_budget=settings.session_token_budget,
            file_store=file_store,
            searchable=req is not None,
        )

        # Current request messages become the "tail" of the assembled context
        current_messages = payload.get("messages", [])

        # ── Dedup: detect retry / reload-retry ──────────────────
        # If the last persisted message already matches the incoming
        # user message, this is a retry.  Skip persistence and drop
        # the duplicate from current_messages so the assembler
        # (which already loads it from history) doesn't double it.
        # A reply that failed or was stopped after that message is
        # replaced: asking again is trying again.
        replaced = False
        if current_messages:
            last_msgs = await self.session_dao.get_recent_messages(
                session_id=sid, limit=2,
            )
            first_cur = current_messages[0]
            cur_content = first_cur.get("content", "")
            if isinstance(cur_content, list):
                cur_content = " ".join(
                    p.get("text", "") for p in cur_content
                    if isinstance(p, dict) and p.get("type") == "text"
                ) or ""

            def same(message: Any) -> bool:
                return message.role == first_cur.get("role") and message.content == cur_content

            if (
                len(last_msgs) == 2
                and last_msgs[-1].role == "assistant"
                and last_msgs[-1].status in TURNS_RETRYABLE
                and same(last_msgs[-2])
            ):
                await self.session_dao.delete_message(message_id=last_msgs[-1].id)
                last_msgs = last_msgs[:-1]
                replaced = True
            # A message that was blocked or failed is not in the history the
            # model gets: sending it again is a new turn, not a retry.
            if last_msgs and same(last_msgs[-1]) and in_context(last_msgs[-1]):
                current_messages = current_messages[1:]

        assembled = await assembler.assemble(
            session_id=sid,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            current_messages=current_messages,
            project=project,
        )

        payload["messages"] = assembled.messages
        _cap_output(payload)
        if req is not None:
            req.attachments = [
                attachment_search.SearchableFile(id=str(a.id), filename=a.filename, text=a.extracted_text or "")
                for a in assembled.searchable
            ]

        # Persist only genuinely new user/system messages
        first_user_text: str | None = None
        for msg in current_messages:
            if msg.get("role") in ("user", "system"):
                content = msg.get("content", "")
                # Handle multimodal content arrays
                content_parts_json = None
                if isinstance(content, list):
                    content_parts_json = content
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ) or ""
                stored = await self.session_dao.append_message(
                    session_id=sid,
                    role=msg["role"],
                    content=content,
                    content_parts_json=content_parts_json,
                )
                if saved is not None and msg["role"] == "user":
                    saved.append(stored.id)
                if msg["role"] == "user" and first_user_text is None:
                    first_user_text = content

        # A session created without a title (the API, the SDK, a script) is
        # named from its first message, as the chat page names the ones it
        # creates; untitled, it stayed "Untitled chat" for good.
        if sess.title is None and first_user_text:
            sess.title = title_from_message(first_user_text)

        # Commit user messages immediately so they survive if the
        # streaming response is interrupted (e.g. page reload).
        if current_messages or replaced:
            await self.session_dao.session.commit()

        return payload, str(sid), (str(sess.project_id) if sess.project_id else None)

    # ── Skills helpers ───────────────────────────────────────────────────────

    async def _resolve_skills(
        self,
        question: str,
        auth: AuthContext,
        session_id: str | None = None,
        requested: list[str] | None = None,
        *,
        file_types: list[str] | None = None,
        inline_files_chars: int = 0,
    ) -> list[ResolvedSkill]:
        """The skills that apply to *question*, for ``_prepare`` to put in.

        Resolved while the session loads, so the session id is the one the
        request named; the skills service does not use it to resolve. It
        resolves among the skills the user can see, which takes their groups:
        a skill shared with a group reaches its members.
        """
        if not self.skills_client:
            return []
        groups: tuple[str, ...] = ()
        if self.group_memberships is not None:
            groups = await self.group_memberships.resolve(auth.user_id) or ()
        result = await self.skills_client.resolve_skills(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            group_ids=groups,
            requested=requested,
            session_id=session_id,
            user_query=question or None,
            file_types=file_types,
            inline_files_chars=inline_files_chars,
        )
        return result.skills

    async def _chat_file_types(self, auth: AuthContext, session_id: str | None) -> list[str]:
        """The types of the files a chat has ("pdf", "xlsx"), for skill triggers.

        The gateway never said which files a chat had, so a skill set to come
        in on a file type never did.
        """
        if not session_id or self.session_dao is None:
            return []
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return []
        try:
            names = await self.session_dao.attachment_names(
                session_id=sid, tenant_id=auth.tenant_id, user_id=auth.user_id,
            )
        except Exception:  # a missing trigger must not fail the chat
            logger.warning("Reading the chat's file types failed", exc_info=True)
            return []
        return _file_types(names)

    def _apply_skill_tool_constraints(
        self,
        payload: dict[str, Any],
        skills: list[ResolvedSkill],
    ) -> dict[str, Any]:
        """Filter the tools array based on skill constraints.

        If any resolved skill specifies ``forbidden_tools``, those tools
        are removed.  If any skill specifies ``allowed_tools``, only the
        union of allowed tools across all skills is kept.
        """
        if not skills:
            return payload

        tools = payload.get("tools")
        if not tools:
            return payload
        if not any(skill.forbidden_tools or skill.allowed_tools for skill in skills):
            return payload
        filtered = [tool for tool in tools if _skills_allow(skills, tool.get("function", {}).get("name", ""))]
        return {**payload, "tools": filtered}

    async def _record_skills_usage(
        self,
        skills: list[ResolvedSkill],
        auth: AuthContext,
        session_id: str | None = None,
    ) -> None:
        """Fire-and-forget usage telemetry for resolved skills."""
        if not self.skills_client or not skills:
            return
        for skill in skills:
            await self.skills_client.record_usage(
                tenant_id=auth.tenant_id,
                skill_id=skill.skill_id,
                version=skill.version,
                session_id=session_id,
                user_id=auth.user_id,
                matched_by=skill.matched_by,
            )

    # ── MCP tool helpers ─────────────────────────────────────────────────────

    async def _chat_tools(
        self,
        mcp_tools: list[dict[str, Any]],
        auth: AuthContext,
        session_id: str | None,
        skills: list[ResolvedSkill],
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """The MCP tools a chat may use, and those it has switched on.

        The model was given every tool in the tenant, whatever the chat had
        switched off or its execution mode allowed: 48 GitHub tools, ~14.7k
        prompt tokens a turn, with one of them left on (release VM,
        2026-09-25). Now it is given the ones switched on -- none, until the
        user or get_available_tools switches one on -- and can find the others.
        A request without a chat session has none on.
        """
        sid = _uuid_or_none(session_id)
        if sid is None:
            usable, on = {t.get("qualified_name") for t in mcp_tools}, set()
        else:
            usable, on = await ToolAvailabilityService(dao=self.dao).chat_mcp_tools(
                session_id=sid, tenant_id=auth.tenant_id, mcp_catalog=mcp_tools,
            )
        findable = [
            t for t in mcp_tools
            if t.get("qualified_name") in usable and _skills_allow(skills, t.get("qualified_name", ""))
        ]
        return findable, on & {t.get("qualified_name") for t in findable}

    @staticmethod
    def _merge_mcp_tools(
        payload: dict[str, Any],
        mcp_tools: list[dict[str, Any]],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Merge MCP tools into the outgoing payload's ``tools`` array."""
        existing = list(payload.get("tools") or [])
        existing.extend(
            t["openai_tool"] if "openai_tool" in t else t for t in mcp_tools
        )
        payload = {**payload, "tools": existing}
        # Ensure tool_choice allows the model to call tools
        if "tool_choice" not in payload:
            payload["tool_choice"] = "auto"
        logger.info(
            "MCP tool injection: %d tool(s) merged for tenant %s: %s",
            len(mcp_tools),
            tenant_id,
            [
                (t.get("openai_tool", t).get("function", {}).get("name", "?") if isinstance(t, dict) else "?")
                for t in mcp_tools
            ],
        )
        return payload

    # ── Tools the gateway runs ───────────────────────────────────────────────

    @staticmethod
    def _offers_system_tools(candidate: Any, payload: dict[str, Any]) -> bool:
        """Whether the model gets the system tools.

        When its route answers with tool calls and the client has not ruled
        tools out.
        """
        return payload.get("tool_choice") != "none" and knowledge.calls_tools(candidate)

    def _run_system_tool(
        self, req: _Prepared, name: str, arguments: dict[str, Any], *, routed: bool,
    ) -> tuple[str, bool] | None:
        """A system tool's answer, or the answer to a call of a tool not offered.

        ``None`` for a tool to run elsewhere. The model need not find a tool
        before it calls it: an MCP tool the chat may use is switched on and
        run, and a name no tool has is searched for, as get_available_tools
        would -- the 0.5B model on the dev stack invented "add_numbers" and
        gave up when told there was no such tool, but never searched.
        """
        can_find = req.system_tools and bool(req.mcp_findable)
        if name == system_tools.FIND and req.system_tools:
            return self._find_tools(req, arguments)
        if name == system_tools.TIME and req.system_tools:
            return system_tools.current_time(arguments)
        if name.startswith(MCP_TOOL_PREFIX):
            if name in req.mcp_picked:  # offered for the question, and used: kept on
                req.mcp_picked.discard(name)
                req.mcp_found.append(name)
            if name in req.mcp_on:
                return None
            if can_find and any(t.get("qualified_name") == name for t in req.mcp_findable):
                self._switch_on(req, [name])
                return None
        elif (
            (name in knowledge.NAMES and req.knowledge)
            or (name == attachment_search.NAME and req.attachments)
            or (name == skill_files.NAME and req.skill_files)
            or (routed and name.startswith(("client.", "server.")))
        ):
            return None
        # By the tool's part of an MCP name: its server's matches all of its tools.
        query = name.split(".", 2)[-1] if name.startswith(MCP_TOOL_PREFIX) else name
        found = system_tools.find(req.mcp_findable, {"query": query}) if can_find else []
        self._switch_on(req, [t.get("qualified_name", "") for t in found])
        return system_tools.unknown_tool(name, found, can_find=can_find), True

    @staticmethod
    def _find_tools(req: _Prepared, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run get_available_tools: the tools it finds are switched on."""
        try:
            found = system_tools.find(req.mcp_findable, arguments)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}), True
        GatewayService._switch_on(req, [tool.get("qualified_name", "") for tool in found])
        return system_tools.found_answer(str(arguments.get("query") or ""), found, req.mcp_findable), False

    @staticmethod
    def _switch_on(req: _Prepared, names: list[str]) -> None:
        """Switch MCP tools on: offered from the next round, and kept on in the chat."""
        for name in names:
            if name not in req.mcp_on:
                req.mcp_on.add(name)
                req.mcp_found.append(name)
                req.mcp_changed = True

    async def _keep_found(self, req: _Prepared) -> None:
        """Keep the tools switched on this round with the chat, as if the user had.

        Its tool panel shows them on, and the tool router, which reads the
        chat's switches, runs them.
        """
        if not req.mcp_found:
            return
        found, req.mcp_found = req.mcp_found, []
        sid = _uuid_or_none(req.session_id)
        if sid is not None:
            await self.dao.upsert_session_tool_overrides(
                sid, [(name, True) for name in found], updated_by="model",
            )
            await self._release_request_connection()

    async def _offering_found(self, req: _Prepared, payload: dict[str, Any]) -> dict[str, Any]:
        """*payload* offering the tools switched on this round, kept with the chat."""
        await self._keep_found(req)
        if not req.mcp_changed:
            return payload
        req.mcp_changed = False
        return _with_mcp_tools(payload, _switched_on(req))

    def _offers_knowledge(self, candidate: Any, payload: dict[str, Any]) -> bool:
        """Whether the model gets the knowledge tools.

        When RAG Lite is on, the client has not ruled tools out, and the
        route's model answers with tool calls -- vLLM refuses a request that
        offers tools otherwise.
        """
        return (
            self.rag_lite_client is not None
            and settings.rag_lite_enabled
            and not settings.rag_enabled
            and payload.get("tool_choice") != "none"
            and knowledge.calls_tools(candidate)
        )

    def _router_session(self, session_id: str | None) -> uuid.UUID | None:
        """The session the tool router runs tools for, when it is in use."""
        if self.tool_router is None or not session_id:
            return None
        try:
            return uuid.UUID(session_id)
        except ValueError:
            return None

    async def _tool_loop(
        self, req: _Prepared, result: UpstreamResult, *, auth: AuthContext, request_id: str,
    ) -> UpstreamResult:
        """Run the gateway's tools until the model answers (a whole answer).

        Each round the model asks for tools, the gateway runs them and asks
        again, at most ``mcp_tool_loop_max_iterations`` times. A round that
        asks for a tool the gateway cannot run -- one the client defined --
        goes back to the client as it is.
        """
        payload = req.egress_payload
        assert payload is not None  # noqa: S101
        for iteration in range(settings.mcp_tool_loop_max_iterations):
            choices = (result.payload or {}).get("choices") or []
            message = choices[0].get("message") if choices else None
            if not message or not message.get("tool_calls"):
                break
            answers = await self._run_tool_calls(
                req, message["tool_calls"], iteration=iteration, auth=auth, request_id=request_id,
            )
            if answers is None:
                break
            req.tool_iterations = iteration + 1
            turn = _assistant_turn(message.get("content"), message["tool_calls"])
            req.tool_exchange.extend([turn, *answers])
            if req.faden_events:
                starts = [faden_events.tool_start(c, _tool_definition_name(c)) for c in message["tool_calls"]]
                req.faden_whole.extend([*starts, *self._take_faden_events(req, [turn, *answers])])
            payload = {**payload, "messages": [*payload["messages"], turn, *answers]}
            payload = await self._offering_found(req, payload)
            result = await self._complete(req, payload)
        return result

    async def _stream_tool_rounds(
        self, req: _Prepared, *, auth: AuthContext, request_id: str,
    ) -> AsyncIterator[bytes]:
        """A streamed answer, with the gateway's tools run between rounds.

        The answer's text reaches the client as it comes, with PII tokens put
        back. A round that ends in tool calls the gateway runs is not shown:
        they are run, and the next round streams on. Calls the client defined
        go to the client, as a streamed answer's tool calls do.
        """
        payload = req.egress_payload
        assert payload is not None and req.decision is not None  # noqa: S101
        usage: dict[str, int] = {}
        head: dict[str, Any] = {}
        for iteration in range(settings.mcp_tool_loop_max_iterations + 1):
            raw = await self.adapter.completion(
                **_candidate_adapter_kwargs(req.decision.candidate), payload=payload, stream=True,
                first_token_timeout_sec=_first_token_wait(req.decision.candidate),
            )
            calls = ToolCalls()
            said: list[str] = []
            ended: dict[str, Any] = {"finish": None, "failed": False}

            async def answer(raw: Any = raw, calls: ToolCalls = calls, said: list[str] = said,
                             ended: dict[str, Any] = ended) -> AsyncIterator[bytes]:
                async for event in sse_events(raw):  # type: ignore[arg-type]
                    if event == DONE:
                        continue
                    assert isinstance(event, dict)  # noqa: S101
                    if "error" in event and not event.get("choices"):
                        ended["failed"] = True
                        yield sse(event)
                        continue
                    own = {k: event[k] for k in ("id", "object", "created", "model") if k in event}
                    head.update(own)
                    _add_usage(usage, event.get("usage"))
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("tool_calls"):
                            calls.add(delta["tool_calls"])
                        if choice.get("finish_reason"):
                            ended["finish"] = choice["finish_reason"]
                        # The model's thinking reaches the client as it comes, as on
                        # a stream without tools: a reasoning model answered after
                        # 1-14 s of nothing in the chat (OpenRouter, 2026-09-27).
                        # It is not part of the turn sent back with the tools' answers.
                        thinking = reasoning_of(delta)
                        if thinking:
                            yield sse({**own, "choices": [{
                                "index": choice.get("index", 0),
                                "delta": {"reasoning_content": thinking},
                                "finish_reason": None,
                            }]})
                        if delta.get("content"):
                            said.append(delta["content"])
                            yield sse({**own, "choices": [{
                                "index": choice.get("index", 0),
                                "delta": {"content": delta["content"]},
                                "finish_reason": None,
                            }]})

            async for chunk in restore_sse(answer(), req.token_mapping):
                yield chunk
            if ended["failed"]:
                yield sse(DONE)
                return

            if calls and iteration < settings.mcp_tool_loop_max_iterations:
                if req.faden_events and not any(
                    _tool_definition_name(c) in req.client_tools for c in calls.calls()
                ):
                    for call in calls.calls():
                        start = faden_events.tool_start(call, _tool_definition_name(call))
                        yield faden_events.sse_event(start)
                screened: dict[str, tuple[str, gov.Verdict]] = {}
                answered: dict[str, bool] = {}
                if not any(_tool_name(c) in req.client_tools for c in calls.calls()):
                    screened = await self._screen_calls(req, calls.calls(), auth=auth, request_id=request_id)
                    async for asking in self._ask_approvals(
                        req, calls.calls(), screened, answered, auth=auth, request_id=request_id,
                    ):
                        yield asking
                answers = await self._run_tool_calls(
                    req, calls.calls(), iteration=iteration, auth=auth, request_id=request_id,
                    screened=screened, answered=answered,
                )
                # The tool router reads the session's policy; the next round is
                # another model call.
                await self._release_request_connection()
                if answers is not None:
                    req.tool_iterations = iteration + 1
                    turn = _assistant_turn("".join(said) or None, calls.calls())
                    req.tool_exchange.extend([turn, *answers])
                    for event in self._take_faden_events(req, [turn, *answers]):
                        yield faden_events.sse_event(event)
                    payload = {**payload, "messages": [*payload["messages"], turn, *answers]}
                    payload = await self._offering_found(req, payload)
                    continue
            if calls:
                # Tools the client defined: its to run, as in any streamed answer.
                yield sse({**head, "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": restore_tool_calls(
                        calls.as_deltas(), req.token_mapping, req.client_tools,
                    )},
                    "finish_reason": None,
                }]})
            req.finish_reason = ended["finish"] or ("tool_calls" if calls else "stop")
            last: dict[str, Any] = {
                **head, "choices": [{"index": 0, "delta": {}, "finish_reason": req.finish_reason}],
            }
            if usage:
                last["usage"] = usage
            yield sse(last)
            yield sse(DONE)
            return

    def _take_faden_events(
        self, req: _Prepared, round_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """The events of a finished gateway round: tool ends, citations, the round itself."""
        if not req.faden_events:
            return []
        events, req.faden_pending = req.faden_pending, []
        events.append(faden_events.gateway_round(_replayable(round_messages, req.token_mapping)))
        return events

    async def _complete(self, req: _Prepared, payload: dict[str, Any]) -> UpstreamResult:
        """One whole model call; an error from upstream is raised, not answered."""
        from faden_gateway.services.gateway.llm_adapter import CompletionResult  # noqa: PLC0415

        assert req.decision is not None  # noqa: S101
        adapter_result = await self.adapter.completion(
            **_candidate_adapter_kwargs(req.decision.candidate), payload=payload, stream=False,
        )
        assert isinstance(adapter_result, CompletionResult)  # noqa: S101
        if adapter_result.status_code >= 400:
            error = (adapter_result.payload or {}).get("error", {})
            raise GatewayError(
                status_code=adapter_result.status_code,
                message=error.get("message") or f"Upstream error {adapter_result.status_code}",
                error_type=error.get("type", "upstream_error"),
                code=error.get("code"),
            )
        return UpstreamResult(
            status_code=adapter_result.status_code, payload=adapter_result.payload, headers={},
        )

    async def _run_tool_calls(
        self,
        req: _Prepared,
        calls: list[dict[str, Any]],
        *,
        iteration: int,
        auth: AuthContext,
        request_id: str,
        screened: dict[str, tuple[str, gov.Verdict]] | None = None,
        answered: dict[str, bool] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Run *calls*; the tool messages that answer them, PII-scanned.

        A call a check refuses is not run: the model is told why (*screened*,
        from :meth:`_screen_calls`; *answered*, what the user said about calls
        that needed approval).

        ``None`` when any of them is not the gateway's to run (a tool the
        client defined): the model's message then goes to the client as it
        is, rather than half-answered.
        """
        router_session = self._router_session(req.session_id)

        # The client runs the tools it defined. The gateway answers any other
        # call, one to a tool nobody offered too: a small model invents names
        # ("add_numbers"), and the call went to a chat page that runs no
        # tools, which showed an empty reply (dev stack, 2026-09-26).
        names = [_tool_name(call) for call in calls]
        if any(name in req.client_tools for name in names):
            return None
        names = [_mcp_name(req, name) for name in names]
        if screened is None:
            screened = await self._screen_calls(req, calls, auth=auth, request_id=request_id)
        answered = answered or {}

        async def run(call: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
            arguments = _mcp_arguments(req, name, _tool_arguments(call))
            started = time.perf_counter()
            row: dict[str, Any] = {"iteration": iteration, "tool_name": name, "mcp_server": None}
            objection = screened.get(call.get("id", ""))
            answer = answered.get(call.get("id", ""))
            if objection is not None and (objection[1].blocked or answer is not True):
                content = await self._refused(
                    req, call, name, objection[1], answer, auth=auth, request_id=request_id,
                )
                is_error = True
                row["denied"] = True
            elif (own := self._run_system_tool(req, name, arguments, routed=router_session is not None)) is not None:
                content, is_error = own
            elif name in knowledge.NAMES:
                # Searched inside Fadenstack, like the session store: with the
                # values tokenize mode took out, or it would look for
                # "[PERSON_1]". A tool elsewhere (MCP) gets the tokens.
                if req.token_mapping:
                    arguments = _restored(arguments, req.token_mapping)
                assert self.rag_lite_client is not None  # noqa: S101
                tools = knowledge.KnowledgeTools(self.rag_lite_client, token=knowledge.reader_token(auth))
                ran = await tools.run(name, arguments)
                content, is_error = ran.content, ran.is_error
            elif name == attachment_search.NAME:
                # The session's own files, searched in memory, with the values
                # tokenize mode took out -- as the knowledge tools are.
                if req.token_mapping:
                    arguments = _restored(arguments, req.token_mapping)
                content, is_error = attachment_search.AttachmentSearch(req.attachments).run(arguments)
            elif name == skill_files.NAME:
                # A file of a skill resolved for this request, and no other.
                assert self.skills_client is not None  # noqa: S101
                reader = skill_files.SkillFiles(self.skills_client, req.skills, tenant_id=auth.tenant_id)
                content, is_error = await reader.run(arguments)
            elif router_session is not None:
                assert self.tool_router is not None  # noqa: S101
                await self._keep_found(req)  # the router reads the chat's switches
                routed = await self.tool_router.route(
                    tool_id=name, arguments=arguments, call_id=call.get("id", ""),
                    session_id=router_session, tenant_id=auth.tenant_id, request_id=request_id,
                    user_id=auth.user_id,
                    # The user approved it here: the device does not ask again.
                    approved=answer is True,
                )
                content, is_error = routed.content, routed.is_error
                row.update(mcp_server=_mcp_server(name), realm=routed.realm, executor=routed.executor)
            else:
                assert self.mcp_client is not None  # noqa: S101
                try:
                    called = await self.mcp_client.call_tool(
                        qualified_name=name, arguments=arguments, tenant_id=auth.tenant_id,
                        request_id=request_id,
                        pii_mode_override=self._mcp_pii_mode_override(req.pii_policy, req.decision),
                    )
                    content, is_error = called.content, called.is_error
                except Exception as exc:  # noqa: BLE001 - the model is told
                    content, is_error = f"Tool call failed: {exc}", True
                row["mcp_server"] = _mcp_server(name)
            row.update(
                latency_ms=int((time.perf_counter() - started) * 1000),
                is_error=is_error,
                error_message=content[:500] if is_error else None,
            )
            if req.faden_events:
                req.faden_pending.append(faden_events.tool_end(call, row))
                cited = None if is_error else faden_events.citations(name, content)
                if cited is not None:
                    req.faden_pending.append(cited)
            return row, {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}

        # Calls the model makes together are independent -- it asked for them
        # before seeing any result -- so they run together: two searches take
        # as long as the slower one, not both. The tool router's run one at a
        # time, as they always have: some of them run on the user's machine.
        pairs = list(zip(calls, names, strict=True))
        if router_session is None:
            done = await _together(*(run(call, name) for call, name in pairs))
        else:
            done = [await run(call, name) for call, name in pairs]
        answers: list[dict[str, Any]] = []
        for row, answer in done:
            req.tool_calls.append(row)
            answers.append(answer)
        return await self._scan_tool_results(req, answers)

    async def _scan_tool_results(self, req: _Prepared, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tool results pass through PII like the rest of what leaves.

        In tokenize mode they continue the request's mapping, so a name in a
        search result is the token the question already used for it.
        """
        if (
            self.pii_client is None
            or req.decision is None
            or req.pii_policy is None
            or not _needs_scan(req.pii_policy, req.decision.candidate)
        ):
            return answers
        # Each text of each answer is scanned apart -- every passage of a
        # search, its query, its sources -- so the PII service's cache, which
        # keeps one analysis per text, knows a passage the next search returns
        # again. Scanned as one JSON blob per answer, every search was analysed
        # afresh: 400-900 ms a round for five passages.
        units: list[str] = []
        rebuilds: list[tuple[int, int, Callable[[list[str]], str]]] = []
        for answer in answers:
            parts, rebuild = _scan_units(str(answer.get("content") or ""))
            rebuilds.append((len(units), len(parts), rebuild))
            units.extend(parts)
        try:
            scanned = await self.pii_client.sanitize(
                payload={"messages": [{"role": "tool", "content": unit} for unit in units]},
                policy=req.pii_policy,
                mode=req.pii_policy.egress.mode,
                token_mapping=req.token_mapping,
            )
        except Exception:
            if req.pii_policy.egress.fail_action == "allow":
                logger.warning("PII scan of tool results failed; fail_action=allow, sending them as they are")
                return answers
            raise GatewayError(
                status_code=502,
                message="PII service unavailable: tool results were not sent to the model.",
                error_type="server_error",
                code="pii_service_unavailable",
            ) from None
        if scanned.token_mapping:
            req.token_mapping = scanned.token_mapping
        texts = [str(m.get("content") or "") for m in scanned.sanitized_payload.get("messages") or []]
        if len(texts) != len(units):
            raise GatewayError(
                status_code=502,
                message="PII scan of tool results came back incomplete: they were not sent to the model.",
                error_type="server_error",
                code="pii_scan_incomplete",
            )
        return [
            {**answer, "content": rebuild(texts[start:start + count])}
            for answer, (start, count, rebuild) in zip(answers, rebuilds, strict=True)
        ]

    async def _persist_assistant_response(
        self,
        *,
        session_id_str: str | None,
        response_payload: dict[str, Any],
        model_alias: str | None = None,
        provider_instance_id: str | None = None,
        trace_id: str | None = None,
        tool_exchange: list[dict[str, Any]] | None = None,
        blocked: tuple[uuid.UUID | None, str | None] | None = None,
    ) -> None:
        """Store the assistant's response message in the session.

        A refused answer is stored as a notice: no text, its decision and HMAC.
        """
        if not session_id_str or not self.session_dao:
            return
        if blocked is not None:
            await self.session_dao.append_message(
                session_id=uuid.UUID(session_id_str), role="assistant", content="",
                model_alias=model_alias, status=TurnStatus.BLOCKED_OUTPUT.value,
                decision_id=blocked[0], content_hmac=blocked[1],
            )
            return

        import uuid as _uuid  # noqa: PLC0415

        try:
            sid = _uuid.UUID(session_id_str)
        except ValueError:
            return

        choices = response_payload.get("choices", [])
        if not choices:
            return

        msg_data = choices[0].get("message", {})
        content = msg_data.get("content", "")
        if not content:
            return

        # Handle multimodal assistant responses
        content_parts_json = None
        if isinstance(content, list):
            content_parts_json = content
            content = " ".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ) or ""

        usage = response_payload.get("usage", {})
        tokens = usage.get("completion_tokens")

        await self.session_dao.append_message(
            session_id=sid,
            role="assistant",
            content=content,
            content_parts_json=content_parts_json,
            model_alias=model_alias,
            provider_instance_id=(
                _uuid.UUID(provider_instance_id) if provider_instance_id else None
            ),
            token_estimate=tokens,
            trace_id=trace_id,
            tool_call_json=_exchange_record(tool_exchange),
        )

    async def _finish_stream(
        self,
        req: _Prepared,
        *,
        auth: AuthContext,
        request_id: str,
        endpoint: str,
        model_alias: str,
        session_id: str | None,
        started: float,
        stats: Any,
        content: str,
        status_code: int,
        error_code: str | None,
        closing: bool,
        stream_buffer: Any,
        buffer_session_id: str | None,
        saved: asyncio.Event,
        interrupted: bool = False,
    ) -> None:
        """What a streamed answer owes once it has ended: the slot, the reply, the audit row, the trace.

        *saved* is set once the reply is stored, whether or not that worked.
        Each step stands alone: one failing does not skip the ones after it.
        """
        # The model is done: its slot first, then what is saved. It was given
        # back last, after the session write, the skills calls and the audit row.
        try:
            await self._release(req)
        except Exception:
            logger.warning("Failed to release the model slot", exc_info=True)
        if req.session_id:
            if req.blocked_output:
                turn_status, turn_error = TurnStatus.BLOCKED_OUTPUT.value, None
            else:
                turn_status, turn_error = _turn_outcome(
                    content=content,
                    error=stats.error if stats is not None else None,
                    status_code=status_code,
                    closing=closing,
                    finish_reason=req.finish_reason,
                    interrupted=interrupted,
                )
            try:
                await self._persist_stream_assistant_response(
                    session_id_str=req.session_id,
                    # A refused answer is not stored, not even the part already sent.
                    content="" if req.blocked_output else content,
                    model_alias=model_alias,
                    provider_instance_id=req.instance_id,
                    trace_id=req.trace_id,
                    token_estimate=stats.usage.completion_tokens if stats is not None else None,
                    tool_exchange=None if req.blocked_output else req.tool_exchange,
                    status=turn_status,
                    error=turn_error,
                    blocked=req.blocked_output,
                    turn=req,
                )
            except Exception:
                logger.warning("Failed to persist streamed assistant response", exc_info=True)
        saved.set()
        # Whoever reads the reply from the buffer -- the chat page, or one that
        # came back -- is done once it is saved, as the live stream is.
        if stream_buffer and buffer_session_id:
            if closing:
                stream_buffer.push(buffer_session_id, _DONE_EVENT)
            stream_buffer.finish(buffer_session_id)
        # Best effort, and nothing need wait for it.
        _in_background(self._record_skills_usage(req.skills, auth, session_id=req.session_id))
        final_error_code = error_code or req.fallback_error_code
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            await self._audit_on_own_session(
                request_id=request_id,
                trace_id=req.trace_id,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                model_alias=model_alias,
                provider_instance_id=req.instance_id,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=latency_ms,
                ttft_ms=stats.ttft_ms if stats is not None else None,
                prompt_tokens=stats.usage.prompt_tokens if stats is not None else None,
                completion_tokens=stats.usage.completion_tokens if stats is not None else None,
                total_tokens=stats.usage.total_tokens if stats is not None else None,
                error_code=final_error_code,
                stream=True,
                provider_name=(
                    req.decision.candidate.litellm_provider
                    if req.decision is not None else None
                ),
                session_id=req.session_id or session_id,
                group_id=req.group_id,
                project_id=req.project_id,
                finish_reason=req.finish_reason,
                retry_count=0,
                skills_used=_skills_used(req.skills),
                rag_context=req.rag_context,
                mcp_tool_call_count=len(req.tool_calls),
                mcp_tool_loop_iterations=req.tool_iterations,
                tool_calls=req.tool_calls,
                **_agent_log_fields(req),
            )
        except Exception:
            logger.warning("Failed to write the request log", exc_info=True)
        if req.trace_context is not None:
            try:
                self.observability.finalize_stream(
                    req.trace_context,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ttft_ms=stats.ttft_ms if stats is not None else None,
                    prompt_tokens=stats.usage.prompt_tokens if stats is not None else None,
                    completion_tokens=stats.usage.completion_tokens if stats is not None else None,
                    total_tokens=stats.usage.total_tokens if stats is not None else None,
                    provider_instance_id=req.instance_id,
                    error_code=final_error_code,
                )
            except Exception:
                logger.warning("Failed to finalize the stream's trace", exc_info=True)

    async def _audit_on_own_session(self, **fields: Any) -> None:
        """Write the request log row on a session of its own, and commit it.

        The request's session is committed and closed by its dependency once
        the response is over; a stream's end can come after that, and a client
        leaving cancelled the flush it was in, which failed that final commit
        too (``PendingRollbackError``) and lost the row.
        """
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

        async with AsyncSession(self.dao.session.bind, expire_on_commit=False) as own:
            audit = AuditService(GatewayDAO(own), pricing_service=self.audit.pricing_service)
            await audit.log(**fields)
            await own.commit()

    async def _check_turn_limits(self, auth: AuthContext, session_id: str) -> None:
        """One reply at a time in a chat, and a few at once per user (``chat_turns_per_user``).

        Replies whose heartbeat stopped (a gateway that went away) do not count.
        """
        assert self.session_dao is not None  # noqa: S101
        alive_since = datetime.now(timezone.utc) - timedelta(seconds=settings.chat_turn_stale_sec)
        running = await self.session_dao.running_turn_sessions(
            tenant_id=auth.tenant_id, user_id=auth.user_id, alive_since=alive_since,
        )
        if any(str(sid) == session_id for sid in running):
            raise GatewayError(
                status_code=409,
                message="A reply is still being written in this chat. Wait for it, or stop it.",
                code="turn_running",
            )
        if len(running) >= settings.chat_turns_per_user:
            raise GatewayError(
                status_code=429,
                message=f"Replies are being written in {len(running)} of your chats. Wait for one to finish.",
                code="too_many_turns",
            )

    async def _run_turn(
        self,
        req: _Prepared,
        stream: AsyncIterator[bytes],
        stats: StreamStats,
        *,
        buffer: StreamBuffer,
        finish: Callable[..., Awaitable[None]],
    ) -> None:
        """A chat page's reply, written to its end whoever reads it.

        Its events go to the buffer, which the page -- and a page that comes
        back -- reads. Leaving does not stop it. A stop (``request_stop``)
        cancels the reading, which closes the call to the model; what was
        written is kept. The reply's own session (``_own_session``) is
        committed and closed at the end.
        """
        sid = req.session_id
        assert sid is not None  # noqa: S101
        written: list[str] = []
        closing = False

        async def read() -> None:
            nonlocal closing
            async for chunk in stream:
                piece, seen = _split_done(chunk)
                closing = closing or seen
                if piece:
                    _accumulate_stream_content(piece, written)
                    buffer.push(sid, piece)

        reading = asyncio.ensure_future(read())
        buffer.reading(sid, reading)
        beating = asyncio.ensure_future(self._keep_turn_alive(req, written))
        status_code, error_code, interrupted = 200, None, False
        try:
            await reading
        except asyncio.CancelledError:
            # A stop cancels the reading; a gateway going away cancels this.
            interrupted = not buffer.stop_requested(sid)
        except Exception:
            logger.warning("A chat reply's stream failed", exc_info=True)
            status_code, error_code, closing = 502, "upstream_stream_failed", True
        finally:
            beating.cancel()
            await asyncio.gather(beating, return_exceptions=True)
            status_code, error_code = _stream_outcome(stats, status_code, error_code)
            session = self.dao.session
            try:
                await finish(
                    "".join(written), status_code, error_code, closing, asyncio.Event(), interrupted=interrupted,
                )
            finally:
                try:
                    await session.commit()
                finally:
                    await session.close()
        if interrupted:
            raise asyncio.CancelledError

    def _own_session(self) -> None:
        """Give the rest of this reply a database session of its own.

        The reply goes on after its request, whose session is committed and
        closed when the request ends (``get_db_session``): a tool round after
        the page left would have used a session being closed under it. The
        data-access objects -- which the router, the tool router and the audit
        hold -- are pointed at the new one.
        """
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

        own = AsyncSession(self.dao.session.bind, expire_on_commit=False)
        self.dao.session = own
        if self.session_dao is not None:
            self.session_dao.session = own

    async def _keep_turn_alive(self, req: _Prepared, written: list[str]) -> None:
        """Save a running reply's text every few seconds: its heartbeat, and what a reload shows."""
        if req.turn_written is not None:
            # Shielded: a short reply cancels this while its row is still
            # being written, and cancelling the write lost the row.
            await asyncio.shield(req.turn_written)
        while True:
            await asyncio.sleep(settings.chat_turn_checkpoint_sec)
            try:
                await self._checkpoint_turn(req, "".join(written))
            except Exception:
                logger.debug("A running reply was not checkpointed", exc_info=True)

    async def _checkpoint_turn(self, req: _Prepared, content: str) -> None:
        """Save a running reply's text so far; a reply that has ended is left alone."""
        if not req.turn_id or self.session_dao is None:
            return
        from sqlalchemy import update  # noqa: PLC0415
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

        async with AsyncSession(self.session_dao.session.bind) as own:
            await own.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.id == uuid.UUID(req.turn_id),
                    ChatMessage.status == TurnStatus.RUNNING.value,
                )
                .values(content=content, updated_at=datetime.now(timezone.utc)),
            )
            await own.commit()

    def _start_turn(self, req: _Prepared, *, model_alias: str) -> None:
        """Write the reply's row (``running``) as the reply starts, beside the model call.

        Its id goes to the client with the stream (``x-faden-turn-id``); the
        end of the stream fills the row in (``_persist_stream_assistant_response``).
        """
        if not req.session_id or not self.session_dao:
            return
        try:
            sid = uuid.UUID(req.session_id)
        except ValueError:
            return
        turn_id = uuid.uuid4()
        req.turn_id = str(turn_id)
        engine = self.session_dao.session.bind

        async def write() -> None:
            from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

            async with AsyncSession(engine, expire_on_commit=False) as own:
                own.add(ChatMessage(
                    id=turn_id,
                    session_id=sid,
                    role="assistant",
                    content="",
                    model_alias=model_alias,
                    trace_id=req.trace_id,
                    status=TurnStatus.RUNNING.value,
                    updated_at=datetime.now(timezone.utc),
                ))
                await own.commit()

        req.turn_written = _kept(write())

    async def _persist_stream_assistant_response(
        self,
        *,
        session_id_str: str,
        content: str,
        model_alias: str | None = None,
        provider_instance_id: str | None = None,
        trace_id: str | None = None,
        token_estimate: int | None = None,
        tool_exchange: list[dict[str, Any]] | None = None,
        status: str = TurnStatus.COMPLETE.value,
        error: str | None = None,
        blocked: tuple[uuid.UUID | None, str | None] | None = None,
        turn: _Prepared | None = None,
    ) -> None:
        """Store how a streamed reply ended in the session: on its row, or as a new one.

        The row written as the reply started (*turn*) is filled in; without
        one, the reply is added. A complete reply with no text (tool calls for
        the client to run) is not kept, as before.

        Uses a fresh, independent DB session so the commit is not tied
        to the request-scoped session (which may already be closed or
        rolled back if the client disconnected mid-stream).
        """
        if not self.session_dao:
            return
        try:
            sid = uuid.UUID(session_id_str)
        except ValueError:
            return
        row_id = uuid.UUID(turn.turn_id) if turn is not None and turn.turn_id else None
        written = False
        if turn is not None and turn.turn_written is not None:
            try:
                await asyncio.shield(turn.turn_written)
                written = True
            except Exception:
                logger.warning("The reply's row was not written as it started", exc_info=True)
        keep = bool(content) or status != TurnStatus.COMPLETE.value

        # Obtain a fresh DB session from the factory stored on session_dao.
        # The request-scoped session may be unusable at this point (client
        # disconnect can close/rollback it), so we create an independent one.
        engine = self.session_dao.session.bind  # AsyncEngine
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

        async with AsyncSession(engine, expire_on_commit=False) as fresh_session:
            row = await fresh_session.get(ChatMessage, row_id) if written and row_id else None
            if not keep:
                if row is not None:
                    await fresh_session.delete(row)
                    await fresh_session.commit()
                return
            if row is None:
                row = ChatMessage(id=row_id or uuid.uuid4(), session_id=sid, role="assistant")
                fresh_session.add(row)
            row.content = content
            row.model_alias = model_alias
            row.provider_instance_id = uuid.UUID(provider_instance_id) if provider_instance_id else None
            row.token_estimate = token_estimate
            row.trace_id = trace_id
            row.tool_call_json = _exchange_record(tool_exchange)
            row.status = status
            row.error = error
            row.decision_id = blocked[0] if blocked else None
            row.content_hmac = blocked[1] if blocked else None
            row.updated_at = datetime.now(timezone.utc)
            await fresh_session.commit()


def _accumulate_stream_content(chunk: bytes, acc: list[str]) -> None:
    """Extract assistant content deltas from an SSE chunk and append to acc."""
    text = chunk.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        choice = parsed.get("choices", [{}])[0] if parsed.get("choices") else None
        if choice:
            delta_content = choice.get("delta", {}).get("content")
            if delta_content:
                acc.append(delta_content)


def _resolve_pii_policy(
    policy: Any | None,
    session_override: Any | None = None,
    *,
    agent_allow_mode_override: bool | None = None,
) -> PIIPolicy | None:
    """The PII policy a request gets: tenant-specific → system default → None.

    With *session_override* (a ``SessionPIIOverrideRow``, or the
    ``SessionPIIOverride`` an agent's conversation asks for) it is merged on
    top by ``resolve_session_pii``, the same helper the chat's Privacy panel
    uses, so what the panel says is what the request gets: the mode switches
    when the agent's setting, the tenant's, or the override's stored weaken
    right allows it.
    """
    return resolve_session_pii(
        policy, session_override, agent_allow_mode_override=agent_allow_mode_override,
    ).effective


def _require_model(payload: dict[str, Any]) -> str:
    model = str(payload.get("model", "")).strip()
    if not model:
        raise GatewayError(
            status_code=400,
            message="Request must include a non-empty model.",
            code="missing_model",
            param="model",
        )
    return model


async def _check_limits(
    *,
    limiter: RateLimiter,
    tenant_id: str,
    payload: dict[str, Any],
    rpm_limit: int | None,
    tpm_limit: int | None,
) -> None:
    rpm = await limiter.check_rpm(tenant_id=tenant_id, limit=rpm_limit)
    if rpm and not rpm.allowed:
        raise _rate_limited("Rate limit exceeded (RPM).", "rate_limit_rpm", rpm.retry_after_sec)
    estimated_tokens = estimate_input_tokens(
        payload.get("input") or payload.get("messages"),
    )
    tpm = await limiter.check_tpm(
        tenant_id=tenant_id, tokens=estimated_tokens, limit=tpm_limit,
    )
    if tpm and not tpm.allowed:
        raise _rate_limited("Rate limit exceeded (TPM).", "rate_limit_tpm", tpm.retry_after_sec)


def _rate_limited(message: str, code: str, retry_after_sec: int) -> GatewayError:
    """A 429 as OpenAI clients read it: ``rate_limit_error``, and when to retry.

    It said ``invalid_request_error`` with no ``Retry-After``, so clients that
    back off on 429 guessed; the window's remaining seconds were computed and
    dropped.
    """
    return GatewayError(
        status_code=429,
        message=message,
        error_type="rate_limit_error",
        code=code,
        headers={"Retry-After": str(max(int(retry_after_sec), 1))},
    )
