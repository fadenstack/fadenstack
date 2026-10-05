"""Request and response models for ``/api/agents`` and ``/api/agent-tokens``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from faden_backend.db.models.agents import AgentStatus, LocalMcpMode, PrincipalType

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


class HostTool(BaseModel):
    """One tool the host application provides, as the agent expects it."""

    name: str = Field(min_length=1, max_length=128)
    schema_sha256: str | None = Field(default=None, max_length=64)
    risk: str | None = Field(default=None, pattern=r"^(read|write|destructive)$")
    approval: str | None = Field(default=None, pattern=r"^(never|ask|always)$")


class AgentPii(BaseModel):
    """What the agent's conversations may change about PII."""

    model_config = ConfigDict(extra="forbid")

    #: Whether a conversation may switch the egress mode (redact, tokenize). Null follows the
    #: tenant's setting on the PII policy page; true or false decides for this agent.
    allow_mode_override: StrictBool | None = None


class AgentVersionFields(BaseModel):
    """What a version defines. A new version starts from the fields sent."""

    instructions: str = Field(default="", max_length=100_000)
    model_alias: str | None = Field(default=None, max_length=256)
    knowledge: list[str] = Field(default_factory=list)
    server_tools: list[str] | None = None
    host_tool_contract: list[HostTool] = Field(default_factory=list)
    local_mcp_mode: LocalMcpMode = LocalMcpMode.DISABLE
    ui: dict[str, Any] = Field(default_factory=dict)
    pii: AgentPii = Field(default_factory=AgentPii)
    extensions: dict[str, Any] = Field(default_factory=dict)


class CreateAgentRequest(BaseModel):
    """A new agent: its address, name and first version."""

    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    #: The first version's definition; a draft until published.
    version: AgentVersionFields = Field(default_factory=AgentVersionFields)


class UpdateAgentRequest(BaseModel):
    """What can change on an agent without a new version."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class AgentVersionDTO(AgentVersionFields):
    """One version of an agent."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    created_at: datetime
    published_at: datetime | None
    created_by: uuid.UUID | None


class AgentDTO(BaseModel):
    """An agent, with the numbers of its published and latest versions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    slug: str
    name: str
    description: str | None
    status: AgentStatus
    published_version: int | None = None
    latest_version: int | None = None
    #: What the caller may do with it.
    can_manage: bool = False
    created_at: datetime
    updated_at: datetime


class GrantEntry(BaseModel):
    """A user or group allowed to use an agent."""

    principal_type: PrincipalType
    principal_id: uuid.UUID


class GrantDTO(GrantEntry):
    """A grant, with who it names."""

    #: The group's name or the user's email, for display.
    label: str | None = None


class ReplaceGrantsRequest(BaseModel):
    """The full list of grants of an agent."""

    grants: list[GrantEntry] = Field(default_factory=list, max_length=1000)


class MemberEntry(BaseModel):
    """A user who looks after an agent."""

    user_id: uuid.UUID
    role: str = "maintainer"


class MemberDTO(MemberEntry):
    """A member, with who it names."""

    #: The user's e-mail address, for display.
    label: str | None = None


class ReplaceMembersRequest(BaseModel):
    """The full list of an agent's members."""

    members: list[MemberEntry] = Field(default_factory=list, max_length=200)


class AccessDTO(BaseModel):
    """Whether a user may use an agent, and why."""

    allowed: bool
    code: str
    detail: str


class UsageDTO(BaseModel):
    """Usage of one agent from the gateway's request log, without content."""

    requests: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    users: int
    conversations: int
    by_day: list[dict[str, Any]]


class IssueTokenRequest(BaseModel):
    """Sign in to an agent on one device."""

    #: The agent's slug or id.
    agent: str = Field(min_length=1, max_length=64)
    device_label: str | None = Field(default=None, max_length=200)


class RefreshTokenRequest(BaseModel):
    """Trade a refresh token for new tokens."""

    refresh_token: str = Field(min_length=16, max_length=256)


class AgentTokensDTO(BaseModel):
    """An access token for one agent and the refresh token that renews it."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    device_id: uuid.UUID
    agent_id: uuid.UUID
    agent: str


class ClientEventDTO(BaseModel):
    """One event an agent's SDK reported: names, counts, durations -- never content."""

    id: uuid.UUID
    type: str
    user_id: str
    device_id: str | None
    conversation_id: str | None
    data: dict[str, Any]
    occurred_at: datetime | None
    created_at: datetime


class AgentTemplateDTO(BaseModel):
    """An agent for one of Fadenstack's own apps, ready to create from."""

    id: str
    #: The app it is for ("Chrome", "Microsoft Word").
    app: str
    slug: str
    name: str
    description: str | None
    tools: int
    #: The body ``POST /api/agents`` takes.
    definition: dict[str, Any]


class AppSeenDTO(BaseModel):
    """An app (one version) that runs the agent, as its SDK reported it; or requests whose tools were held."""

    source: str
    name: str
    version: str
    sdk: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    reports: int
    last_user_id: str
    #: The e-mail address of who reported it last.
    last_user_label: str | None = None
    tools: list[str]
    context: list[dict[str, Any]]


class ToolReviewDTO(BaseModel):
    """One tool the apps bring, or the contract lists: what each says, and what to do."""

    name: str
    #: ``new``, ``changed``, ``riskier``, ``unused`` or ``same``.
    status: str
    #: The app's class for it (``unknown`` when it gives none): a suggestion.
    suggested_class: str
    contract_class: str | None
    in_contract: bool
    description: str | None
    schema_sha256: str | None
    contract_schema_sha256: str | None
    open_world: bool | None
    apps: list[str]
    last_seen_at: datetime | None
    #: ``instructions``, ``hidden_characters``, ``link``, ``long``: hints about the description.
    warnings: list[str]


class AppsReviewDTO(BaseModel):
    """What the apps that run the agent bring, against its latest version."""

    compared_with: int | None
    #: The latest version lists no host tools, so it allows any.
    unrestricted: bool
    apps: list[AppSeenDTO]
    tools: list[ToolReviewDTO]
    #: The greeting and suggestions the newest app would show, when the version's differ.
    ui: dict[str, Any] | None
    #: Tools waiting for a decision (new, changed, riskier).
    pending: int
    published_version: int | None = None
    #: The same against the published version: what is held back until a version with it is published.
    live_pending: int = 0


class AcceptedTool(BaseModel):
    """A tool to take into the contract, with the class the admin gives it."""

    name: str = Field(min_length=1, max_length=128)
    #: ``unknown`` leaves it unclassified: it always asks.
    risk: Literal["read", "write", "destructive", "unknown"]


class AcceptAppsRequest(BaseModel):
    """What to take from the apps into a new version."""

    tools: list[AcceptedTool] = Field(default_factory=list, max_length=200)
    #: Contract tools to take out (no app brings them any more).
    remove: list[str] = Field(default_factory=list, max_length=200)
    #: Take the apps' greeting and suggestions.
    ui: bool = False
    #: Publish the new version at once.
    publish: bool = False


class SignInDTO(BaseModel):
    """One sign-in (device) to an agent."""

    device_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID
    device_label: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    idle_expires_at: datetime


class DeveloperModeRequest(BaseModel):
    """Switch a user's developer mode."""

    enabled: bool
    #: When it switches itself off; null keeps it on until switched off.
    until: datetime | None = None


class DeveloperModeDTO(BaseModel):
    """A user's developer mode."""

    user_id: uuid.UUID
    developer_mode: bool
    developer_mode_until: datetime | None
