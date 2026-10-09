from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    """Minimal core contract for chat completions."""

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    session_id: str | None = None

    model_config = ConfigDict(extra="allow")


class EmbeddingsRequest(BaseModel):
    """Minimal core contract for embeddings endpoint."""

    model: str = Field(min_length=1)
    input: Any

    model_config = ConfigDict(extra="allow")


class RerankRequest(BaseModel):
    """Score documents against a query: the Cohere / Jina / vLLM rerank shape."""

    model: str = Field(min_length=1)
    query: str = Field(min_length=1)
    documents: list[str | dict[str, Any]] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)
    return_documents: bool = True

    model_config = ConfigDict(extra="allow")


class ImagesRequest(BaseModel):
    """Pictures from a prompt: OpenAI's ``/v1/images/generations``, and the settings local servers take.

    No ``model``: the tenant's image model. Pictures come back in the answer (``b64_json``), never as links.
    """

    model: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1, max_length=4000)
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = Field(default=None, pattern=r"^\d{3,4}x\d{3,4}$")
    response_format: Literal["b64_json"] = "b64_json"
    negative_prompt: str | None = Field(default=None, max_length=2000)
    num_inference_steps: int | None = Field(default=None, ge=1, le=100)
    guidance_scale: float | None = Field(default=None, ge=0, le=30)
    seed: int | None = None

    model_config = ConfigDict(extra="ignore")


class ModelObject(BaseModel):
    """OpenAI model object shape."""

    id: str
    object: str = "model"
    created: int
    owned_by: str


class ListModelsResponse(BaseModel):
    """OpenAI list models response shape."""

    object: str = "list"
    data: list[ModelObject]


# ---------------------------------------------------------------------------
# Tool Routing DTOs
# ---------------------------------------------------------------------------


class ExecutionModeEnum(StrEnum):
    """Execution mode values for API DTOs."""

    LOCAL_ONLY = "local_only"
    SERVER_ONLY = "server_only"
    HYBRID = "hybrid"


class ToolAvailabilityDTO(BaseModel):
    """Single tool entry in the effective tool catalog."""

    tool_id: str
    display_name: str | None = None
    description: str | None = None
    realm: str
    source: str
    effective_enabled: bool
    policy_allowed: bool
    user_enabled: bool
    available: bool
    availability_reason: str | None = None
    #: What the tool does: read, write, destructive or unknown (tool_classes.py).
    tool_class: str = "unknown"
    #: Where the class came from: rule, annotations, gateway, agent or none.
    class_source: str = "none"
    #: Reaches outside the organisation (recorded; no mode acts on it yet).
    open_world: bool | None = None
    #: What the chat's tool mode does with it: run, ask or hidden.
    mode_decision: str = "ask"
    mode_reason: str | None = None


class SessionToolOverrideDTO(BaseModel):
    """A single tool override in a policy patch request."""

    tool_id: str
    enabled: bool


class SessionToolPolicyDTO(BaseModel):
    """Session tool policy read/write shape."""

    session_id: str
    execution_mode: ExecutionModeEnum = ExecutionModeEnum.SERVER_ONLY
    hybrid_preference: str | None = None
    effective_catalog_version: int = 0
    #: What the user picked for the chat (``ask`` until they pick).
    tool_mode: str = "ask"
    #: The mode that applies: the pick, narrowed by any ceiling (EE).
    effective_tool_mode: str = "ask"
    tool_mode_ceiling: str | None = None


class SessionToolPolicyPatchDTO(BaseModel):
    """Payload for PATCH /v1/sessions/{session_id}/tool-policy."""

    execution_mode: ExecutionModeEnum | None = None
    hybrid_preference: str | None = None
    tool_overrides: list[SessionToolOverrideDTO] | None = None
    tool_mode: Literal["off", "read_only", "ask", "auto"] | None = None


class ToolAvailabilityResponse(BaseModel):
    """Response for GET /v1/tools/available."""

    session_id: str | None = None
    execution_mode: ExecutionModeEnum
    effective_catalog_version: int = 0
    tools: list[ToolAvailabilityDTO] = Field(default_factory=list)
    #: The chat's tool mode, as SessionToolPolicyDTO reports it.
    tool_mode: str = "ask"
    effective_tool_mode: str = "ask"
    tool_mode_ceiling: str | None = None


# ---------------------------------------------------------------------------
# Session PII Policy DTOs
# ---------------------------------------------------------------------------


class SessionPIIOverrideDTO(BaseModel):
    """Partial override values the user has set on a session."""

    pii_enabled: bool | None = None
    egress_enabled_for_cloud: bool | None = None
    egress_enabled_for_local: bool | None = None
    egress_mode: str | None = None
    egress_fail_action: str | None = None
    telemetry_enabled: bool | None = None
    telemetry_mode: str | None = None
    presidio_threshold: float | None = Field(None, ge=0.0, le=1.0)
    presidio_entities_add: list[str] | None = None


class SessionPIIPolicyDTO(BaseModel):
    """GET response for session PII policy."""

    session_id: str
    has_override: bool
    override: SessionPIIOverrideDTO | None = None
    floor: dict[str, Any] | None = None
    #: What the chat's requests get (``resolve_session_pii``), not what the override asks for.
    effective: dict[str, Any] | None = None
    #: A mode the chat chooses is applied: the tenant allows it, or the override
    #: was saved with the right to weaken.
    mode_switch_allowed: bool = False
    #: The caller may choose the mode now: it is allowed, or the caller may weaken.
    can_switch_mode: bool = False
    #: False when the override asks for a mode its requests do not get.
    mode_applied: bool = True


class SessionPIIPolicyPatchDTO(BaseModel):
    """PATCH request for session PII policy override."""

    pii_enabled: bool | None = None
    egress_enabled_for_cloud: bool | None = None
    egress_enabled_for_local: bool | None = None
    egress_mode: str | None = None
    egress_fail_action: str | None = None
    telemetry_enabled: bool | None = None
    telemetry_mode: str | None = None
    presidio_threshold: float | None = Field(None, ge=0.0, le=1.0)
    presidio_entities_add: list[str] | None = None


# ---------------------------------------------------------------------------
# Tool Call Audit / Trace DTOs
# ---------------------------------------------------------------------------


class RouteDecisionDTO(BaseModel):
    """Captures one routing decision for tracing."""

    call_id: str
    tool_id: str
    realm: str
    executor: str
    session_id: str
    policy_decision: str  # "allow" | "deny" | "approval_required"
    denial_reason: str | None = None


class ToolCallTraceDTO(BaseModel):
    """A single tool call entry in an execution trace."""

    call_id: str
    tool_id: str
    realm: str
    executor: str
    is_error: bool
    latency_ms: int
    content_preview: str | None = None  # first 200 chars


class ToolExecutionTraceDTO(BaseModel):
    """Full trace of all tool calls within a single request."""

    request_id: str
    session_id: str
    execution_mode: str
    total_tool_calls: int = 0
    total_iterations: int = 0
    tool_calls: list[ToolCallTraceDTO] = Field(default_factory=list)
    route_decisions: list[RouteDecisionDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Client Capability DTOs
# ---------------------------------------------------------------------------


class ClientCapabilityDTO(BaseModel):
    """A single client-advertised tool."""

    tool_id: str
    client_id: str
    realm: str
    available: bool
    tool_schema: dict[str, Any] | None = None  # was schema_json; pydantic BaseModel reserves `.schema_json()`


class ClientHandshakeDTO(BaseModel):
    """Payload for client capability registration."""

    session_id: str
    client_id: str
    client_type: str | None = None
    client_version: str | None = None
    trust_level: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Approval DTOs
# ---------------------------------------------------------------------------


class ApprovalRequestDTO(BaseModel):
    """A pending tool call requiring user approval."""

    call_id: str
    tool_id: str
    realm: str
    sensitivity: str
    reason: str
    arguments_preview: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponseDTO(BaseModel):
    """User response to an approval request."""

    call_id: str
    approved: bool
    reason: str | None = None
