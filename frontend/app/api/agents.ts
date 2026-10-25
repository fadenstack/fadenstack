/**
 * Agents API (`/api/agents`): agents, their immutable versions, who may use
 * them (grants) and who looks after them (maintainers), and what they did
 * (usage, the SDKs' events, device sign-ins). See docs/help/pages/administer/agents.md.
 *
 * Every route answers for the caller's rights: maintainers manage their own
 * agents, `agents:manage` manages all of them, `agents:delete` deletes retired
 * ones.
 */
import { json, query, requester, segment } from "./http";
import { principalIds, type PrincipalOption, type PrincipalType } from "./principals";

const request = requester("/api/agents");

// ── Types (mirror backend/faden_backend/web/api/agents/schema.py) ─────────

export type AgentStatus = "draft" | "published" | "retired";
export type LocalMcpMode = "disable" | "allow";
/** A host tool's class; absent: unclassified (always asks). */
export type ToolRisk = "read" | "write" | "destructive";
export type ToolApproval = "never" | "ask" | "always";

/** A tool the host application provides, as the agent expects it. */
export interface HostTool {
  name: string;
  risk?: ToolRisk;
  approval?: ToolApproval;
  schema_sha256?: string;
}

/** What a version defines. Versions never change: a change is a new version. */
/** What the agent's conversations may change about PII. */
export interface AgentPii {
  /** Whether a conversation may switch the PII mode (redact, tokenize); null follows the tenant's setting. */
  allow_mode_override: boolean | null;
}

export interface AgentDefinition {
  instructions: string;
  model_alias: string | null;
  knowledge: string[];
  /** The gateway's own tools the model may be offered (names or patterns); null: what a chat gets. */
  server_tools: string[] | null;
  host_tool_contract: HostTool[];
  local_mcp_mode: LocalMcpMode;
  /** What the SDKs show: `greeting`, `suggestions`. */
  ui: Record<string, unknown>;
  pii: AgentPii;
  /** Enterprise settings; kept as they are. */
  extensions: Record<string, unknown>;
}

export interface AgentVersion extends AgentDefinition {
  id: string;
  version: number;
  created_at: string;
  published_at: string | null;
  created_by: string | null;
}

export interface Agent {
  id: string;
  tenant_id: string;
  slug: string;
  name: string;
  description: string | null;
  status: AgentStatus;
  published_version: number | null;
  latest_version: number | null;
  /** The caller maintains it, or manages every agent. */
  can_manage: boolean;
  /**
   * Settings and restrictions no installed extension enforces: until one does, only the maintainers can use
   * the agent. Told to those who manage it.
   */
  unenforced?: string[];
  /** Those of them that are restrictions on the agent, which its managers can lift. */
  unenforced_restrictions?: string[];
  created_at: string;
  updated_at: string;
}

export interface AgentGrant {
  principal_type: PrincipalType;
  principal_id: string;
  /** The group's name or the user's e-mail. */
  label?: string | null;
}

export interface AgentMember {
  user_id: string;
  role: string;
  label?: string | null;
}

export interface AccessExplanation {
  allowed: boolean;
  code: string;
  detail: string;
}

export interface AgentUsage {
  requests: number;
  errors: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  users: number;
  conversations: number;
  by_day: { day: string; requests: number; total_tokens: number }[];
}

/** What the SDKs reported -- names, counts, durations, never content. */
export type ClientEventType = "tool.executed" | "approval.decided" | "local_mcp.attached" | "session.stats";

export interface ClientEvent {
  id: string;
  type: ClientEventType | string;
  user_id: string;
  device_id: string | null;
  conversation_id: string | null;
  data: Record<string, unknown>;
  occurred_at: string | null;
  created_at: string;
}

/** One device signed in to the agent. */
export interface AgentSignIn {
  device_id: string;
  agent_id: string;
  user_id: string;
  device_label: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  idle_expires_at: string;
}

/** A class an app gives a tool; `unknown` when it gives none (the tool always asks). */
export type ToolClass = ToolRisk | "unknown";
export type ToolReviewStatus = "new" | "changed" | "riskier" | "unused" | "same";

/** An app (one version) that runs the agent, as its SDK reported it; or the requests whose tools were held back. */
export interface AppSeen {
  source: "app" | "request";
  name: string;
  version: string;
  sdk: string | null;
  first_seen_at: string;
  last_seen_at: string;
  reports: number;
  last_user_id: string;
  last_user_label: string | null;
  tools: string[];
  context: { id: string; title?: string; kind?: string }[];
}

/** One tool the apps bring, or the contract lists: what each says. */
export interface ToolReview {
  name: string;
  status: ToolReviewStatus;
  /** The app's class for it: a suggestion. */
  suggested_class: ToolClass;
  contract_class: ToolRisk | null;
  in_contract: boolean;
  description: string | null;
  schema_sha256: string | null;
  contract_schema_sha256: string | null;
  open_world: boolean | null;
  /** "Chrome extension 0.2.0", or "requests" for tools held back from requests. */
  apps: string[];
  last_seen_at: string | null;
  /**
   * `instructions`, `hidden_characters`, `link`, `long`: hints about the description; `unmarked_writes`: it writes
   * but declares no output, so what it writes is not marked as AI-generated.
   */
  warnings: string[];
}

/** What the apps bring, against the agent's latest version. */
export interface AppsReview {
  compared_with: number | null;
  /** The latest version lists no host tools, so it allows any. */
  unrestricted: boolean;
  apps: AppSeen[];
  tools: ToolReview[];
  ui: { greeting?: string; suggestions?: string[] } | null;
  /** Tools waiting for a decision against the latest version. */
  pending: number;
  published_version: number | null;
  /** The same against the published version: what the gateway still holds back. */
  live_pending: number;
}

export interface AcceptAppsPayload {
  tools: { name: string; risk: ToolClass }[];
  remove: string[];
  ui: boolean;
  publish: boolean;
}

/** An agent for one of Fadenstack's own apps, ready to create from. */
export interface AgentTemplate {
  id: string;
  /** "Chrome", "Microsoft Word". */
  app: string;
  slug: string;
  name: string;
  description: string | null;
  tools: number;
  /** The body `POST /api/agents` takes. */
  definition: Record<string, unknown>;
}

export interface CreateAgentPayload {
  slug: string;
  name: string;
  description?: string | null;
  version?: Partial<AgentDefinition>;
}

// ── Routes ───────────────────────────────────────────────────────────────

/** An agent addressed by its slug (or id). */
const at = (agent: string) => `/${segment(agent)}`;

export const agentsApi = {
  list: () => request<Agent[]>(""),
  /** Agents for Fadenstack's own apps (Chrome, Office), ready to create from. */
  templates: () => request<AgentTemplate[]>("/templates"),
  get: (agent: string) => request<Agent>(at(agent)),
  create: (payload: CreateAgentPayload) => request<Agent>("", json("POST", payload)),
  update: (agent: string, changes: { name?: string; description?: string | null }) =>
    request<Agent>(at(agent), json("PATCH", changes)),
  /** Closed to everyone; versions, grants and history stay. */
  retire: (agent: string) => request<Agent>(`${at(agent)}/retire`, json("POST")),
  restore: (agent: string) => request<Agent>(`${at(agent)}/restore`, json("POST")),
  /** Gone for good, with its versions, access and sign-ins (`agents:delete`; a retired agent only). */
  remove: (agent: string) => request<void>(at(agent), json("DELETE")),

  versions: (agent: string) => request<AgentVersion[]>(`${at(agent)}/versions`),
  version: (agent: string, number: number) => request<AgentVersion>(`${at(agent)}/versions/${number}`),
  /** A new draft version. */
  addVersion: (agent: string, definition: AgentDefinition) =>
    request<AgentVersion>(`${at(agent)}/versions`, json("POST", definition)),
  /** Serve this version to the agent's users; an older one rolls back. */
  publish: (agent: string, number: number) =>
    request<Agent>(`${at(agent)}/versions/${number}/publish`, json("POST")),

  grants: (agent: string) => request<AgentGrant[]>(`${at(agent)}/grants`),
  setGrants: (agent: string, grants: Pick<AgentGrant, "principal_type" | "principal_id">[]) =>
    request<AgentGrant[]>(`${at(agent)}/grants`, json("PUT", { grants })),
  members: (agent: string) => request<AgentMember[]>(`${at(agent)}/members`),
  setMembers: (agent: string, members: Pick<AgentMember, "user_id" | "role">[]) =>
    request<AgentMember[]>(`${at(agent)}/members`, json("PUT", { members })),
  /** Whether a user may use the agent, and why. */
  explainAccess: (agent: string, userId: string) =>
    request<AccessExplanation>(`${at(agent)}/access/${segment(userId)}`),
  /** Lift a restriction nothing enforces: everyone granted can use the agent again. */
  liftRestriction: (agent: string, name: string) =>
    request<Agent>(`${at(agent)}/restrictions/${segment(name)}`, json("DELETE")),

  /** The apps that run the agent and what they bring, against its latest version. */
  apps: (agent: string) => request<AppsReview>(`${at(agent)}/apps`),
  /** A new version with what the apps bring, as chosen; published at once if asked. */
  acceptApps: (agent: string, payload: AcceptAppsPayload) =>
    request<AgentVersion>(`${at(agent)}/apps/accept`, json("POST", payload)),

  usage: (agent: string, days: number) => request<AgentUsage>(`${at(agent)}/usage${query({ days })}`),
  events: (agent: string, filter: { type?: string; limit?: number } = {}) =>
    request<ClientEvent[]>(`${at(agent)}/events${query({ type: filter.type, limit: filter.limit ?? 200 })}`),
  signIns: (agent: string) => request<AgentSignIn[]>(`${at(agent)}/sign-ins`),

  /** Users and groups to grant to or make maintainers: by search text, or by id. */
  findPrincipals: (q: string) => request<PrincipalOption[]>(`/principals${query({ q })}`),
  namePrincipals: (principals: { type: PrincipalType; id: string }[]) =>
    principals.length === 0
      ? Promise.resolve([])
      : request<PrincipalOption[]>(`/principals${query({ ids: principalIds(principals) })}`),
};

// ── Rights ───────────────────────────────────────────────────────────────

/**
 * Maintaining an agent is a right on that agent, not an RBAC permission;
 * `/users/me/access` says whether the user has it (`maintains_agents`), and
 * the UI gates on it under this key like on a permission.
 */
export const AGENT_MAINTAINER = "agents:maintain";

/** Any of these opens the agents pages. */
export const AGENTS_PAGE_KEYS = ["agents:read", "agents:create", "agents:manage", AGENT_MAINTAINER];

/** What the user may do with agents in general; each agent says itself whether they manage it (`can_manage`). */
export function agentRights(can: (permission: string) => boolean) {
  return {
    open: AGENTS_PAGE_KEYS.some((key) => can(key)),
    create: can("agents:create"),
    /** Every agent of the tenant; otherwise only those the user manages. */
    seeAll: can("agents:read") || can("agents:manage"),
    /** Delete retired agents for good: a role's right, not the agent's maintainers'. */
    delete: can("agents:delete"),
  };
}

/** Ends a device's sign-in (`/api/agent-tokens/{device}`): its user, or the agent's maintainers. */
export const revokeSignIn = (deviceId: string) =>
  requester("/api/agent-tokens")<void>(`/${segment(deviceId)}`, json("DELETE"));
