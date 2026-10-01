/**
 * Agents API (`/api/agents`): agents, their immutable versions, who may use
 * them (grants) and who looks after them (maintainers), and what they did
 * (usage, the SDKs' events, device sign-ins). See docs/agents.md.
 *
 * Every route answers for the caller's rights: maintainers manage their own
 * agents, `agents:manage` manages all of them.
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
  get: (agent: string) => request<Agent>(at(agent)),
  create: (payload: CreateAgentPayload) => request<Agent>("", json("POST", payload)),
  update: (agent: string, changes: { name?: string; description?: string | null }) =>
    request<Agent>(at(agent), json("PATCH", changes)),
  /** Closed to everyone; versions, grants and history stay. */
  retire: (agent: string) => request<Agent>(at(agent), json("DELETE")),
  restore: (agent: string) => request<Agent>(`${at(agent)}/restore`, json("POST")),

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

/** Ends a device's sign-in (`/api/agent-tokens/{device}`): its user, or the agent's maintainers. */
export const revokeSignIn = (deviceId: string) =>
  requester("/api/agent-tokens")<void>(`/${segment(deviceId)}`, json("DELETE"));
