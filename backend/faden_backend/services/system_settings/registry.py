"""Code-defined settings registry and validation helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from faden_backend.db.models.system_settings import SystemApplyScope
from faden_backend.settings import settings

SettingType = Literal["string", "int", "bool", "secret", "json", "enum"]


class OutOfRangeError(ValueError):
    """A value of the right type outside the setting's range (``min_value``-``max_value``)."""


@dataclass(frozen=True)
class SettingDefinition:
    """Registry definition for one setting key."""

    key: str
    type: SettingType
    category: str
    group: str
    label: str
    description: str
    is_secret: bool
    default: object
    apply_scope: SystemApplyScope
    service_targets: tuple[str, ...]
    protected: bool = False
    enum_values: tuple[str, ...] = ()
    #: The range of an ``int`` setting, both ends included; ``None``: open on that side.
    min_value: int | None = None
    max_value: int | None = None
    #: A further check of a value of the right type; raises ``ValueError`` when it is not one.
    check: Callable[[object], None] | None = None


def check_time_zone(value: object) -> None:
    """Refuse anything but an IANA zone name the tz database knows (``Europe/Berlin``, ``UTC``).

    ``zoneinfo`` reads names as paths under the tz database, so a name shaped like a path is refused before.
    """
    name = str(value)
    if not name or len(name) > 64 or ".." in name or name.startswith(("/", "\\")):
        msg = f"Not a time zone name: {name!r}."
        raise ValueError(msg)
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"Not a time zone name: {name!r}."
        raise ValueError(msg) from exc


def _usage(
    key: str,
    type_: SettingType,
    group: str,
    label: str,
    description: str,
    default: object,
    **extra: object,
) -> SettingDefinition:
    """A ``usage.*`` setting: read live by the backend and by the gateway's rollup job at each run."""
    return SettingDefinition(
        key=key,
        type=type_,
        category="usage",
        group=group,
        label=label,
        description=description,
        is_secret=False,
        default=default,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
        **extra,  # type: ignore[arg-type]
    )


#: Usage metering and its privacy controls (GDPR Art. 5(1)(c), (e), 25(2)): the defaults and ranges of the
#: compliance report's section 9. The gateway's rollup job reads them from this database at each run.
USAGE_SETTINGS: list[SettingDefinition] = [
    _usage(
        "usage.person_reporting", "bool", "privacy", "Person-level reporting about others",
        "Lets holders of the usage auditor role see the usage of named people, with a reason that is "
        "recorded (M02, M06, M07). Off: nobody sees anyone's usage but their own. Needs Root Mode to "
        "change; where there is a works council, agree it with them first.",
        False, protected=True,
    ),
    _usage(
        "usage.min_group_size", "int", "privacy", "Smallest group shown (people)",
        "A figure about fewer people than this is not shown on its own (M04): it joins a 'fewer than k "
        "people' row. 5 to 50.",
        5, min_value=5, max_value=50,
    ),
    _usage(
        "usage.person_granularity", "enum", "privacy", "Per-person figures by",
        "day: per-person figures are kept and shown per day; month: per month (M10). Never by the hour.",
        "day", enum_values=("day", "month"),
    ),
    _usage(
        "usage.request_log_retention_days", "int", "retention", "Keep the request log (days)",
        "One row per request: who, when, which model, tokens, cost; never its content. 7 to 365 days (M09).",
        90, min_value=7, max_value=365,
    ),
    _usage(
        "usage.person_retention_days", "int", "retention", "Keep per-person figures (days)",
        "Each person's daily figures. 31 to 400 days; more than 90 needs a documented reason (M09, S06).",
        90, min_value=31, max_value=400,
    ),
    _usage(
        "usage.hourly_retention_days", "int", "retention", "Keep hourly figures (days)",
        "Hourly figures per team, agent, model and provider, without people. 31 to 180 days.",
        90, min_value=31, max_value=180,
    ),
    _usage(
        "usage.daily_retention_months", "int", "retention", "Keep daily figures (months)",
        "Daily figures per team, agent, model and provider, without people. 13 to 60 months.",
        25, min_value=13, max_value=60,
    ),
    _usage(
        "usage.time_zone", "string", "time", "Time zone of days and months",
        "Where a day and a month of usage begin and end, e.g. Europe/Berlin (an IANA name).",
        "UTC", check=check_time_zone,
    ),
]


SETTINGS_REGISTRY: list[SettingDefinition] = [
    SettingDefinition(
        key="llm.residency.internal_networks",
        type="string",
        category="llm",
        group="data_residency",
        label="Internal networks",
        description=(
            "Networks that count as your own on the data residency map, in addition to the private "
            "ranges (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7), which always do. Comma-separated "
            "CIDRs, e.g. the public range of your own datacenter: 203.0.113.0/24."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="api.server.endpoint_url",
        type="string",
        category="api_server",
        group="endpoint",
        label="Endpoint URL",
        description="Swagger/OpenAPI URL for faden_gateway docs.",
        is_secret=False,
        default="/gateway-docs/docs",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="api.server.container_name",
        type="string",
        category="api_server",
        group="endpoint",
        label="API Container Name",
        description="Container name used for endpoint lifecycle operations.",
        is_secret=False,
        default="faden-gateway",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="faden_gateway.jwt_secret",
        type="secret",
        category="auth",
        group="jwt",
        label="LLM API JWT Secret",
        description="JWT verification secret for faden_gateway.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    SettingDefinition(
        key="faden_backend.users_secret",
        type="secret",
        category="auth",
        group="jwt",
        label="Backend Users JWT Secret",
        description="Secret used to sign user API tokens. Both backend and API must agree on this value.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    SettingDefinition(
        key="faden_gateway.langfuse_enabled",
        type="bool",
        category="observability",
        group="langfuse",
        label="Langfuse Enabled",
        description="Enable gateway-to-Langfuse observability export.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.langfuse_host",
        type="string",
        category="observability",
        group="langfuse",
        label="Langfuse Host",
        description="Langfuse URL reachable by faden_gateway.",
        is_secret=False,
        default="http://langfuse-web:3000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.langfuse_public_key",
        type="secret",
        category="observability",
        group="langfuse",
        label="Langfuse Public Key",
        description="Langfuse project public key.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.langfuse_secret_key",
        type="secret",
        category="observability",
        group="langfuse",
        label="Langfuse Secret Key",
        description="Langfuse project secret key.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    SettingDefinition(
        key="shared.redis.password",
        type="secret",
        category="database",
        group="redis",
        label="Redis Password",
        description="Redis auth password used by shared services.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.STACK_RECREATE,
        service_targets=("redis", "langfuse-web", "langfuse-worker", "faden-gateway"),
        protected=True,
    ),
    SettingDefinition(
        key="shared.postgres.password",
        type="secret",
        category="database",
        group="postgres",
        label="Postgres Password",
        description="Postgres admin password for the shared stack.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.STACK_RECREATE,
        service_targets=("postgres", "langfuse-web", "langfuse-worker"),
        protected=True,
    ),
    SettingDefinition(
        key="shared.grafana.admin_password",
        type="secret",
        category="logging",
        group="grafana",
        label="Grafana Admin Password",
        description="Grafana local admin password.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("grafana",),
        protected=True,
    ),
    # The Hugging Face token is not a generic secret. It is checked with the
    # Hub, refused under the published master key, audited and kept in
    # ``services/llm/hf_token.py``, behind ``/api/llm/settings/hf-token``.
    # ── PII module settings ─────────────────────────────────────
    SettingDefinition(
        key="faden_gateway.pii_enabled",
        type="bool",
        category="modules",
        group="pii",
        label="PII Module Enabled",
        description="Enable PII detection and redaction via the PII service.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.pii_service_url",
        type="string",
        category="modules",
        group="pii",
        label="PII Service URL",
        description="Internal URL of the PII micro-service (e.g. http://faden-pii:8000 in Docker, http://127.0.0.1:8003/api in dev).",
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.pii_default_policy",
        type="json",
        category="modules",
        group="pii",
        label="Default PII Policy",
        description=(
            "Default PII policy JSON applied when no tenant-specific policy exists. "
            "Structure: {telemetry: {enabled, mode}, egress: {enabled_for_cloud, mode, fail_action}, "
            "presidio: {language, threshold, entities}}"
        ),
        is_secret=False,
        default={},
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    # ── MCP module settings ──────────────────────────────────
    SettingDefinition(
        key="faden_gateway.mcp_enabled",
        type="bool",
        category="modules",
        group="mcp",
        label="MCP Module Enabled",
        description="Enable the MCP Tool Registry for governed external tool access.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.mcp_service_url",
        type="string",
        category="modules",
        group="mcp",
        label="MCP Service URL",
        description=(
            "Internal URL of the MCP micro-service "
            "(e.g. http://faden-mcp:8000 in Docker, "
            "http://127.0.0.1:8007 in dev)."
        ),
        is_secret=False,
        default="http://faden-mcp:8000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.mcp_service_token",
        type="secret",
        category="modules",
        group="mcp",
        label="MCP Service Token",
        description="Shared bearer token between API gateway and MCP service.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    # ── Skills module settings ───────────────────────────────
    SettingDefinition(
        key="faden_gateway.skills_enabled",
        type="bool",
        category="modules",
        group="skills",
        label="Skills Module Enabled",
        description="Enable the Skills Registry for reusable LLM instruction management.",
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.skills_service_url",
        type="string",
        category="modules",
        group="skills",
        label="Skills Service URL",
        description=(
            "Internal URL of the Skills micro-service "
            "(e.g. http://faden-skills:8000 in Docker, "
            "http://127.0.0.1:8008 in dev)."
        ),
        is_secret=False,
        default="http://faden-skills:8000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.skills_service_token",
        type="secret",
        category="modules",
        group="skills",
        label="Skills Service Token",
        description="Shared bearer token between API gateway and Skills service.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    # ── Provenance module settings ───────────────────────────
    # The switch is the module's (Settings > Modules): switching it off there asks for
    # confirmation and is recorded; this route refuses to switch it off.
    SettingDefinition(
        key="faden_gateway.provenance_enabled",
        type="bool",
        category="modules",
        group="provenance",
        label="Provenance Module Enabled",
        description=(
            "Mark AI-generated content as such in a machine-readable way (EU AI Act Art. 50(2)). "
            "On by default."
        ),
        is_secret=False,
        default=True,
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.provenance_service_url",
        type="string",
        category="modules",
        group="provenance",
        label="Provenance Service URL",
        description=(
            "Internal URL of the provenance engine "
            "(e.g. http://faden-provenance:8000 in Docker, "
            "http://127.0.0.1:8009 in dev)."
        ),
        is_secret=False,
        default="http://faden-provenance:8000",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
    ),
    SettingDefinition(
        key="faden_gateway.provenance_service_token",
        type="secret",
        category="modules",
        group="provenance",
        label="Provenance Service Token",
        description="Shared bearer token the gateway and the backend present to the provenance engine.",
        is_secret=True,
        default="",
        apply_scope=SystemApplyScope.SERVICE_RESTART,
        service_targets=("faden-gateway",),
        protected=True,
    ),
    # The public check page (/check): read from the database on every request
    # (services/provenance/checks.py), so every worker follows a change at once.
    SettingDefinition(
        key="provenance.public_check_enabled",
        type="bool",
        category="modules",
        group="provenance",
        label="Public check page",
        description=(
            "Offer the page /check, where anyone can check a picture or a file for marks without an "
            "account (10 checks a minute per address, up to 20 MB; nothing submitted is kept). Texts are "
            "checked only for people with an account. On by default: pictures and files can reach the "
            "public, and for them the Code of Practice asks for open detection."
        ),
        is_secret=False,
        default=True,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="provenance.public_check_contact",
        type="string",
        category="modules",
        group="provenance",
        label="Contact for text checks",
        description=(
            "Shown on the public check page to people who want a text checked, e.g. an e-mail address "
            "(up to 500 characters). Empty: the page names none."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    # ── RAG Lite ─────────────────────────────────────────────
    SettingDefinition(
        key="rag_lite.enabled",
        type="bool",
        category="modules",
        group="rag_lite",
        label="RAG Lite Enabled",
        description=(
            "Enable the embedded RAG Lite module (pgvector-only, no external "
            "RAG service). Ignored when the full RAG module is enabled."
        ),
        is_secret=False,
        default=False,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_provider_id",
        type="string",
        category="modules",
        group="rag_lite",
        label="Embedding Provider",
        description=(
            "UUID of the LLM provider to use for embeddings. "
            "Leave empty to auto-detect (first provider with "
            "supports_embeddings=true)."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_model",
        type="string",
        category="modules",
        group="rag_lite",
        label="Embedding Model Name",
        description=(
            "Model name for the /v1/embeddings endpoint. "
            "Leave empty to use the provider's configured model."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.hybrid_search",
        type="bool",
        category="modules",
        group="rag_lite",
        label="Hybrid Search",
        description=(
            "Search by keyword and by vector, and fuse the two rankings. "
            "Keywords find exact terms (codes, names) an embedding can blur."
        ),
        is_secret=False,
        default=True,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.rerank_provider_id",
        type="string",
        category="modules",
        group="rag_lite",
        label="Reranker Provider",
        description=(
            "Provider of a reranking (scoring) model. When set, the search "
            "candidates are re-scored by it. Leave empty for no reranking."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.rerank_model",
        type="string",
        category="modules",
        group="rag_lite",
        label="Reranker Model Name",
        description="Model name for /v1/rerank. Leave empty to use the provider's model.",
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.rerank_template",
        type="string",
        category="modules",
        group="rag_lite",
        label="Reranker Prompt Template",
        description=(
            "How the query and documents are wrapped for the reranker: 'auto' "
            "picks by model name, 'qwen3' (Qwen3-Reranker's instruction "
            "template), or 'none'."
        ),
        is_secret=False,
        default="auto",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="chat.deleted_retention_days",
        type="int",
        category="chat",
        group="retention",
        label="Keep deleted chats (days)",
        description=(
            "How long a chat its person deleted is kept before it is erased for good, with its files and "
            "memory. 0 erases at the next hourly pass. Chats deleted before the upgrade to this release count "
            "from the upgrade. An administrator's delete in the console erases at once."
        ),
        is_secret=False,
        default=30,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.rerank_candidates",
        type="int",
        category="modules",
        group="rag_lite",
        label="Reranker Candidates",
        description=(
            "How many search candidates the reranker re-scores. Its time grows with "
            "them; on RAGBench 10 ranked as well as 30 in 40% of the time."
        ),
        is_secret=False,
        default=10,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.embedding_dim",
        type="int",
        category="modules",
        group="rag_lite",
        label="Embedding Dimension",
        description="Actual embedding dimension returned by the model (e.g. 768, 1536).",
        is_secret=False,
        default=768,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.chunk_max_tokens",
        type="int",
        category="modules",
        group="rag_lite",
        label="Chunk Max Tokens",
        description="Maximum tokens per text chunk (approx. 4 chars/token).",
        is_secret=False,
        default=512,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.chunk_overlap_tokens",
        type="int",
        category="modules",
        group="rag_lite",
        label="Chunk Overlap Tokens",
        description="Overlap tokens between consecutive chunks.",
        is_secret=False,
        default=64,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.file_store_root",
        type="string",
        category="modules",
        group="rag_lite",
        label="File Store Root Path",
        description="Local filesystem path for storing uploaded RAG Lite documents.",
        is_secret=False,
        default="/data/faden/rag-lite",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="rag_lite.upload_max_file_mb",
        type="int",
        category="modules",
        group="rag_lite",
        label="Max Upload File Size (MB)",
        description="Maximum file upload size in megabytes for RAG Lite.",
        is_secret=False,
        default=20,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="users.login_lifetime_s",
        type="int",
        category="auth",
        group="users",
        label="Sign-in lifetime (s)",
        description=(
            "How long a sign-in to the console lasts before the person signs in again. "
            "0 means a sign-in never expires."
        ),
        is_secret=False,
        default=604800,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="users.api_token_default_lifetime_s",
        type="int",
        category="auth",
        group="users",
        label="API token lifetime (s)",
        description="How long an API token lasts when the person making it names no lifetime.",
        is_secret=False,
        default=7776000,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="users.api_token_max_lifetime_s",
        type="int",
        category="auth",
        group="users",
        label="Longest API token lifetime (s)",
        description="The longest an API token may last. Every API token expires.",
        is_secret=False,
        default=31536000,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="agents.access_token_ttl_s",
        type="int",
        category="auth",
        group="agents",
        label="Agent access token lifetime (s)",
        description=(
            "How long an agent access token is valid. The SDK refreshes it; removing a user's "
            "access takes effect within this time."
        ),
        is_secret=False,
        default=600,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="agents.refresh_token_ttl_s",
        type="int",
        category="auth",
        group="agents",
        label="Agent sign-in lifetime (s)",
        description="How long an agent sign-in on a device lasts before the user signs in again.",
        is_secret=False,
        default=2592000,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="agents.refresh_idle_s",
        type="int",
        category="auth",
        group="agents",
        label="Agent sign-in idle timeout (s)",
        description="An agent sign-in not used for this long ends.",
        is_secret=False,
        default=604800,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="updates.check_enabled",
        type="bool",
        category="updates",
        group="update_check",
        label="Check for new releases",
        description=(
            "Once a day, read the public list of Fadenstack releases and show administrators when an update "
            "is out. Nothing about this server is sent: the list is fetched like any file and compared here."
        ),
        is_secret=False,
        # What `faden deploy` asked (FADEN_UPDATE_CHECK in .env), until an administrator changes it here.
        default=settings.updates_check_enabled,
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="updates.channel",
        type="enum",
        category="updates",
        group="update_check",
        label="Release channel",
        description=(
            "stable: releases only. preview: release candidates too, to try a release before it is out."
        ),
        is_secret=False,
        default="stable",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
        enum_values=("stable", "preview"),
    ),
    SettingDefinition(
        key="updates.feed_url",
        type="string",
        category="updates",
        group="update_check",
        label="Release list address",
        description=(
            "Where the list of releases is read. Empty: https://fadenstack.com/updates/v1/index.json. "
            "For a site without internet, a copy on your own web server (index.json and index.json.sig, "
            "copied as they are)."
        ),
        is_secret=False,
        default=settings.updates_feed_url,  # FADEN_UPDATE_FEED_URL in .env, until changed here
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    SettingDefinition(
        key="updates.proxy_url",
        type="string",
        category="updates",
        group="update_check",
        label="Proxy for the release list",
        description=(
            "An HTTP(S) proxy for reading the release list only, e.g. http://proxy.example:3128. Empty: none."
        ),
        is_secret=False,
        default="",
        apply_scope=SystemApplyScope.LIVE_RELOAD,
        service_targets=(),
    ),
    *USAGE_SETTINGS,
]


def extend_registry(*definitions: SettingDefinition) -> None:
    """Append additional setting definitions (used by EE plugins)."""
    SETTINGS_REGISTRY.extend(definitions)


def registry_by_key() -> dict[str, SettingDefinition]:
    """Return mapping of key -> setting definition."""
    return {item.key: item for item in SETTINGS_REGISTRY}


def validate_value(defn: SettingDefinition, value: object) -> object:
    """Validate value against setting type and constraints.

    Raises ``OutOfRangeError`` (a ``ValueError``) for a number outside the setting's range, ``ValueError``
    for anything else wrong. Every route that writes a setting comes through here, so a range (the smallest
    group shown, M04) cannot be stepped round by writing the key some other way.
    """
    checked = _validate_type(defn, value)
    if defn.check is not None:
        defn.check(checked)
    return checked


def _check_range(defn: SettingDefinition, value: int) -> None:
    if defn.min_value is None and defn.max_value is None:
        return
    if isinstance(value, bool):  # True is an int to Python; not a number of anything here
        msg = f"Setting {defn.key} expects integer value."
        raise ValueError(msg)
    if (defn.min_value is not None and value < defn.min_value) or (
        defn.max_value is not None and value > defn.max_value
    ):
        msg = f"Setting {defn.key} takes {defn.min_value}-{defn.max_value}."
        raise OutOfRangeError(msg)


def _validate_type(defn: SettingDefinition, value: object) -> object:  # noqa: C901
    if defn.type in {"string", "secret"}:
        if not isinstance(value, str):
            msg = f"Setting {defn.key} expects string value."
            raise ValueError(msg)
        return value
    if defn.type == "int":
        if not isinstance(value, int):
            msg = f"Setting {defn.key} expects integer value."
            raise ValueError(msg)
        _check_range(defn, value)
        return value
    if defn.type == "bool":
        if not isinstance(value, bool):
            msg = f"Setting {defn.key} expects boolean value."
            raise ValueError(msg)
        return value
    if defn.type == "json":
        if not isinstance(value, (dict, list)):
            msg = f"Setting {defn.key} expects object or array value."
            raise ValueError(msg)
        return value
    if defn.type == "enum":
        if not isinstance(value, str) or value not in defn.enum_values:
            msg = f"Setting {defn.key} expects one of: {', '.join(defn.enum_values)}."
            raise ValueError(msg)
        return value
    msg = f"Unsupported setting type for key {defn.key}."
    raise ValueError(msg)
