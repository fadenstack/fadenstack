import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_gateway.db.base import Base
from faden_gateway.db.crypto import EncryptedJSON, EncryptedText


class ProviderType(enum.StrEnum):
    """Gateway provider instance type."""

    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    TGI = "tgi"
    OLLAMA = "ollama"
    REMOTE_OPENAI = "remote_openai"
    REMOTE_ANTHROPIC = "remote_anthropic"
    REMOTE_GOOGLE = "remote_google"
    REMOTE_BEDROCK = "remote_bedrock"
    REMOTE_AZURE = "remote_azure"
    REMOTE_MISTRAL = "remote_mistral"
    REMOTE_GROQ = "remote_groq"
    REMOTE_DEEPSEEK = "remote_deepseek"
    REMOTE_COHERE = "remote_cohere"
    REMOTE_CUSTOM = "remote_custom"


class ProviderHealthStatus(enum.StrEnum):
    """Health of a provider instance."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class PrivacyMode(enum.StrEnum):
    """Tracing payload policy for tenant data."""

    FULL = "full"
    REDACTED = "redacted"
    METADATA_ONLY = "metadata_only"


class SessionStatus(enum.StrEnum):
    """Chat session lifecycle status."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemoryFactScope(enum.StrEnum):
    """Scope of a durable memory fact."""

    SESSION = "session"
    PROJECT = "project"
    USER = "user"


class MemoryFactStatus(enum.StrEnum):
    """Lifecycle status of a memory fact."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Tool Routing Enums
# ---------------------------------------------------------------------------


class ExecutionMode(enum.StrEnum):
    """Session-level tool execution mode."""

    LOCAL_ONLY = "local_only"
    SERVER_ONLY = "server_only"
    HYBRID = "hybrid"


class ToolRealm(enum.StrEnum):
    """Where a tool executes."""

    SERVER_MANAGED = "server_managed"
    MCP_REMOTE = "mcp_remote"
    CLIENT_LOCAL = "client_local"
    CLIENT_PROXIED = "client_proxied"


class ToolSource(enum.StrEnum):
    """Origin of a tool registration."""

    CORE = "core"
    SKILLS = "skills"
    MCP = "mcp"
    LOCAL_AGENT = "local_agent"
    PLUGIN = "plugin"


class LLMModelAlias(Base):
    """Logical model aliases exposed on /v1/models."""

    __tablename__ = "llm_model_alias"

    alias: Mapped[str] = mapped_column(String(256), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_parameters: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LLMProviderInstance(Base):
    """Concrete upstream instance that can serve aliases."""

    __tablename__ = "llm_provider_instance"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    type: Mapped[ProviderType] = mapped_column(
        SAEnum(
            ProviderType,
            name="gateway_provider_type",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    health_status: Mapped[ProviderHealthStatus] = mapped_column(
        SAEnum(
            ProviderHealthStatus,
            name="gateway_provider_health_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ProviderHealthStatus.UNKNOWN,
    )
    api_key_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        doc="Fernet-encrypted provider API key.",
    )
    # ── Outbound TLS (per provider) ────────────────────────────────
    # One of: "default" (inherit global), "verify" (verify against
    # system trust + optional ca bundle), "verify_custom_ca",
    # "insecure" (skip verification — requires
    # tls_outbound_allow_insecure=true to actually disable).
    ssl_verify_mode: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        doc="Per-provider SSL verification mode for litellm.",
    )
    ssl_ca_bundle_pem: Mapped[str | None] = mapped_column(
        EncryptedText("tls-outbound-ca"), nullable=True,
        doc="Encrypted PEM bundle of trusted CAs for this provider.",
    )
    ssl_client_cert_pem: Mapped[str | None] = mapped_column(
        EncryptedText("tls-outbound-cert"), nullable=True,
        doc="Encrypted PEM client certificate for mTLS to this provider.",
    )
    ssl_client_key_pem: Mapped[str | None] = mapped_column(
        EncryptedText("tls-outbound-cert"), nullable=True,
        doc="Encrypted PEM client private key for mTLS to this provider.",
    )
    litellm_provider: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        doc="LiteLLM provider prefix (e.g. 'anthropic', 'vertex_ai').",
    )
    litellm_model: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
        doc="LiteLLM model identifier (e.g. 'claude-sonnet-4-20250514').",
    )
    extra_params: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
        doc="Provider-specific params (region, project_id, api_version, custom headers).",
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        doc="Optional backend-assigned node id for node-managed local runtimes.",
    )
    node_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Node execution metadata (execution_target, desired_state, labels).",
    )
    capacity_hints: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Backend-provided placement/capacity hints for future routing heuristics.",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    source_kind: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="Generic inference source discriminator (e.g. 'runtime', 'inference_deployment').",
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        doc="Logical parent identifier (e.g. InferenceDeployment.id or LLMRuntime.id).",
    )

    __table_args__ = (
        Index("ix_llm_provider_instance_source", "source_kind", "source_id"),
        Index(
            "uq_llm_provider_instance_source",
            "source_kind",
            "source_id",
            unique=True,
            postgresql_where=text("source_kind IS NOT NULL AND source_id IS NOT NULL"),
            sqlite_where=text("source_kind IS NOT NULL AND source_id IS NOT NULL"),
        ),
    )


class LLMPoolMembership(Base):
    """Mapping of alias -> provider instances with optional weight override."""

    __tablename__ = "llm_pool_membership"

    model_alias: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("llm_model_alias.alias", ondelete="CASCADE"),
        primary_key=True,
    )
    provider_instance_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_provider_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weight_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TenantLLMPolicy(Base):
    """Tenant policy controls model/provider access, limits and PII."""

    __tablename__ = "tenant_llm_policy"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    privacy_mode: Mapped[PrivacyMode] = mapped_column(
        SAEnum(
            PrivacyMode,
            name="gateway_privacy_mode",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PrivacyMode.METADATA_ONLY,
    )
    allowed_model_aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    allowed_provider_types: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True,
    )
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ----- PII policy (nullable = feature disabled) -----
    pii_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "JSON PII policy: "
            "{telemetry: {enabled, mode, store_raw}, "
            " egress: {enabled_for_cloud, enabled_for_local, mode, fail_action}, "
            " presidio: {language, threshold, entities}}"
        ),
    )
    allow_mode_override: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="When True, session overrides may switch egress mode (redact ↔ tokenize).",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LLMGatewayRequestLog(Base):
    """Audit log for routed gateway requests."""

    __tablename__ = "llm_gateway_request_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Cost estimate columns (populated at write time by PricingService)
    estimated_input_cost: Mapped[float | None] = mapped_column(
        Numeric(18, 10), nullable=True,
    )
    estimated_output_cost: Mapped[float | None] = mapped_column(
        Numeric(18, 10), nullable=True,
    )
    estimated_total_cost: Mapped[float | None] = mapped_column(
        Numeric(18, 10), nullable=True,
    )
    currency: Mapped[str | None] = mapped_column(
        String(3), server_default="USD", nullable=True,
    )
    price_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("price_catalog.id", ondelete="SET NULL"),
        nullable=True,
    )
    cost_estimate_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
    )
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stream: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Pipeline observability columns
    session_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
    )
    # Who the usage is attributed to beyond the user: the usage group the
    # backend stores on the user, and the chat project the session is in.
    group_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # A request of an agent (``/v1/agents/{agent}/...``): the agent, the device
    # signed in to it, and the conversation and turn the SDK names -- the
    # conversation itself stays on the device.
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default="0",
    )
    skills_used: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
    )
    rag_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True,
    )
    mcp_tool_call_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default="0",
    )
    mcp_tool_loop_iterations: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class LLMToolCallLog(Base):
    """Per-tool-call telemetry linked to a gateway request."""

    __tablename__ = "llm_tool_call_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    request_log_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_gateway_request_log.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    mcp_server: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class PriceCatalog(Base):
    """Model pricing catalog for cost estimation."""

    __tablename__ = "price_catalog"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    input_price_per_1k: Mapped[float] = mapped_column(
        Numeric(12, 8), nullable=False,
    )
    output_price_per_1k: Mapped[float] = mapped_column(
        Numeric(12, 8), nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), server_default="USD", nullable=False,
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
    )
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------------------------------------------------------------------------
# Chat: Projects, Sessions, Messages
# ---------------------------------------------------------------------------


class ChatProject(Base):
    """A project groups related sessions and scopes memory."""

    __tablename__ = "chat_project"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_instructions: Mapped[str | None] = mapped_column(
        EncryptedText("chat-content"), nullable=True,
    )
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_chat_project_tenant_user", "tenant_id", "user_id"),
    )


class ChatSession(Base):
    """A single chat conversation thread."""

    __tablename__ = "chat_session"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_project.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Encrypted at rest like the messages: a title is the start of the first
    # one. Rows from before read as they were (EncryptedText passes plaintext).
    title: Mapped[str | None] = mapped_column(EncryptedText("chat-title"), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(
            SessionStatus,
            name="chat_session_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        SAEnum(
            ExecutionMode,
            name="session_execution_mode",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ExecutionMode.SERVER_ONLY,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_chat_session_tenant_user_status", "tenant_id", "user_id", "status"),
    )


class SessionPIIOverrideRow(Base):
    """Per-session PII policy override (1:1 with chat_session)."""

    __tablename__ = "session_pii_override"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pii_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    egress_enabled_for_cloud: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    egress_enabled_for_local: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    egress_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    egress_fail_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telemetry_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    telemetry_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    presidio_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    presidio_entities_add: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )


class ChatMessage(Base):
    """An individual message within a chat session."""

    __tablename__ = "chat_message"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(EncryptedText("chat-content"), nullable=False)
    content_parts_json: Mapped[list[Any] | None] = mapped_column(
        EncryptedJSON("chat-content"), nullable=True,
    )
    tool_call_json: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON("chat-content"), nullable=True,
    )
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: A reply is a turn with a state of its own (``TurnStatus``): the row is
    #: written when the reply starts, so a failure is kept, not lost with the
    #: browser tab that saw it. Every other message is ``complete``, or
    #: ``blocked_input`` / ``blocked_output`` when governance refused it.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="complete", server_default="complete",
    )
    #: Why the reply failed, as the chat shows it.
    error: Mapped[str | None] = mapped_column(EncryptedText("chat-content"), nullable=True)
    #: When the reply last moved: a running turn's heartbeat.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: A blocked message keeps an empty ``content``: the text is never stored,
    #: only ``content_hmac`` (keyed per tenant) and the decision.
    decision_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    content_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), index=True,
    )

    __table_args__ = (
        # The turns still running, for the sweeper that ends the ones a
        # restart left behind. Partial: every other row is complete.
        Index("ix_chat_message_running", "updated_at", postgresql_where=text("status = 'running'")),
    )


class TurnStatus(str, enum.Enum):
    """Where a reply is (``ChatMessage.status``)."""

    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    #: Stopped by the user; what was written is kept.
    STOPPED = "stopped"
    #: Ended by the gateway going away (a restart) while it was written.
    INTERRUPTED = "interrupted"
    #: A prompt governance refused: the admin's message, no text.
    BLOCKED_INPUT = "blocked_input"
    #: An answer governance refused: the question stays, the answer goes.
    BLOCKED_OUTPUT = "blocked_output"


#: Why an interrupted reply ended, as the chat stores it.
TURN_INTERRUPTED_ERROR = "The server stopped while this reply was being written."

#: Messages that are not part of the conversation the model is sent.
TURNS_LEFT_OUT = frozenset({
    TurnStatus.RUNNING.value, TurnStatus.FAILED.value, TurnStatus.INTERRUPTED.value,
    TurnStatus.BLOCKED_INPUT.value, TurnStatus.BLOCKED_OUTPUT.value,
})
#: Replies that can be tried again.
TURNS_RETRYABLE = frozenset({
    TurnStatus.FAILED.value, TurnStatus.STOPPED.value, TurnStatus.INTERRUPTED.value,
})


# ---------------------------------------------------------------------------
# Summaries & Memory Facts
# ---------------------------------------------------------------------------


class SessionSummary(Base):
    """Rolling summary of a session's conversation."""

    __tablename__ = "session_summary"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary_text: Mapped[str] = mapped_column(
        EncryptedText("chat-content"), nullable=False,
    )
    last_message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False,
    )
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class MemoryFact(Base):
    """A durable memory fact extracted from or manually added to a session."""

    __tablename__ = "memory_fact"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[MemoryFactScope] = mapped_column(
        SAEnum(
            MemoryFactScope,
            name="memory_fact_scope",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=MemoryFactScope.SESSION,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_project.id", ondelete="SET NULL"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(EncryptedText("chat-memory"), nullable=False)
    value: Mapped[str] = mapped_column(EncryptedText("chat-memory"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True,
    )
    status: Mapped[MemoryFactStatus] = mapped_column(
        SAEnum(
            MemoryFactStatus,
            name="memory_fact_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=MemoryFactStatus.CANDIDATE,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_memory_fact_scope_lookup",
            "tenant_id", "user_id", "scope", "status",
        ),
    )


# ---------------------------------------------------------------------------
# Chat Attachments
# ---------------------------------------------------------------------------


class AttachmentScope(enum.StrEnum):
    """Scope of a chat attachment."""

    SESSION = "session"
    PROJECT = "project"


class ExtractionStatus(enum.StrEnum):
    """Text extraction status for an attachment."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ChatAttachment(Base):
    """A file attached to a chat session or project."""

    __tablename__ = "chat_attachment"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_project.id", ondelete="CASCADE"),
        nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_message.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(
        EncryptedText("chat-attachments"), nullable=True,
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        SAEnum(
            ExtractionStatus,
            name="chat_extraction_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ExtractionStatus.PENDING,
    )
    scope: Mapped[AttachmentScope] = mapped_column(
        SAEnum(
            AttachmentScope,
            name="chat_attachment_scope",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=AttachmentScope.SESSION,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_chat_attachment_session", "session_id"),
        Index("ix_chat_attachment_project", "project_id"),
        Index("ix_chat_attachment_message", "message_id"),
        Index("ix_chat_attachment_tenant_user", "tenant_id", "user_id"),
    )


# ---------------------------------------------------------------------------
# Session Tool Policy & Overrides
# ---------------------------------------------------------------------------


class SessionExecutionPolicy(Base):
    """Per-session execution mode and hybrid preference."""

    __tablename__ = "session_execution_policy"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        SAEnum(
            ExecutionMode,
            name="session_execution_mode",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ExecutionMode.SERVER_ONLY,
    )
    hybrid_preference: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    #: What the user lets tools do in this chat (``off``, ``read_only``,
    #: ``ask``, ``auto``); ``None`` is the default, ``ask``.
    tool_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    catalog_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )


class SessionToolOverride(Base):
    """Per-session per-tool enable/disable toggle."""

    __tablename__ = "session_tool_override"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[str] = mapped_column(
        String(512), primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )


class SessionClientCapability(Base):
    """Tools advertised by a connected local client for a given session."""

    __tablename__ = "session_client_capability"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        primary_key=True,
    )
    client_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    tool_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    realm: Mapped[ToolRealm] = mapped_column(
        SAEnum(
            ToolRealm,
            name="tool_realm",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ToolRealm.CLIENT_LOCAL,
    )
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class AgentClientEvent(Base):
    """What an SDK reports about its agent's work on a device -- never content.

    Tool executions, approval decisions, attached local MCP servers, session
    statistics: names, counts, durations and outcomes, for usage and audit.
    Arguments, results and messages are refused when the event arrives.
    """

    __tablename__ = "agent_client_event"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: When it happened on the device.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AgentClientManifest(Base):
    """What an app that runs an agent brings: its tools, context and defaults -- once per distinct manifest.

    An SDK reports it when it starts (``source`` ``app``); tools the gateway held back from a request because
    the agent's contract does not list them come in as ``request``. The console compares them with the
    agent's contract for review: nothing here changes what the agent may do.
    """

    __tablename__ = "agent_client_manifest"
    __table_args__ = (UniqueConstraint("agent_id", "sha256", name="uq_agent_client_manifest_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: SHA-256 of the manifest as stored (canonical JSON).
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``app`` (the SDK reported it) or ``request`` (tools held back from a request).
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    app_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    app_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    reports: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


class PolicyDecisionRecord(Base):
    """A governance check objected: what, where and why -- never the text.

    ``content_hmac`` is HMAC-SHA256 of the refused text with the tenant's key:
    the same prompt refused twice shows as the same value, and nobody can read
    it or test guesses against it without the key.
    """

    __tablename__ = "llm_gateway_policy_decision"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: ``input``, ``output`` or ``tool``.
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``block``, ``approval_required``, ``approved`` or ``rejected``.
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The policy's name: for auditors, never shown to the user.
    policy_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What the user was told (the admin's message for the policy).
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    check: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Every objection, the one that won first: ``[{check, outcome, policy_id, category}]``.
    reasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True,
    )


class GovernanceKey(Base):
    """A tenant's key for ``content_hmac``, made on first use."""

    __tablename__ = "llm_gateway_governance_key"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(EncryptedText("governance-key"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class ToolRule(Base):
    """A tenant's rule for tools the gateway runs or a client offers.

    ``message`` is what the user is told; ``name`` is for admins and
    auditors. The lowest ``priority`` that matches decides.
    """

    __tablename__ = "llm_gateway_tool_rule"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    #: A glob over tool names: ``mcp_github__*``, ``knowledge_search``, ``*``.
    tool_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    #: ``deny``, ``approval_required`` or ``allow``.
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="tool_rule")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


class ToolClassification(Base):
    """An admin's word on what tools do: their class, or "trust the server's annotations".

    ``tool_class`` is ``read``, ``write``, ``destructive`` or ``annotations``;
    the lowest ``priority`` that matches decides. ``open_world`` is recorded
    and shown; no mode acts on it yet.
    """

    __tablename__ = "llm_gateway_tool_classification"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    #: A glob over tool names: ``mcp.github.*``, ``read_page``.
    tool_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_class: Mapped[str] = mapped_column(String(16), nullable=False)
    open_world: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
