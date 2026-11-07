/**
 * Governance for administrators (`/api/admin/governance`): tool rules (deny
 * or ask, by tool name), tool classes (what a tool does, which the tool
 * modes act on), and the decisions the checks made. The gateway reads rules
 * at most 30 s old. See docs/help/pages/administer/policies.md.
 */
import { json, query, requester, segment } from "./http";

const request = requester("/api/admin/governance");

/** `allow` is only on older rules: it changes nothing, and the server takes no new one. */
export type ToolRuleAction = "deny" | "approval_required" | "allow";
/** `annotations`: trust what these tools' MCP server says about them. */
export type ToolClassValue = "read" | "write" | "destructive" | "annotations";
export type DecisionStage = "input" | "output" | "tool";

/** What the server adds to every rule. */
export interface Stored {
  id: string;
  tenant_id: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ToolRuleFields {
  name: string;
  /** A glob over tool names: `mcp.github.*`, `knowledge_search`, `*`. */
  tool_pattern: string;
  action: ToolRuleAction;
  /** What the user is told. */
  message: string;
  category: string;
  /** Orders rules of the same action; a deny wins over ask whatever the priority. */
  priority: number;
  enabled: boolean;
}

export type ToolRule = ToolRuleFields &
  Stored & {
    /** An older `allow` rule: it changes nothing. */
    no_effect: boolean;
  };

export interface ToolClassFields {
  tool_pattern: string;
  tool_class: ToolClassValue;
  /** Reaches outside the organisation: recorded and shown; no mode acts on it yet. */
  open_world: boolean | null;
  note: string | null;
  priority: number;
}

export type ToolClassRule = ToolClassFields & Stored;

export interface PolicyDecision {
  id: string;
  user_id: string | null;
  agent_id: string | null;
  session_id: string | null;
  request_id: string | null;
  stage: DecisionStage;
  outcome: string;
  policy_id: string | null;
  category: string | null;
  message: string | null;
  check: string | null;
  /** A keyed hash of refused text: "the same text again", nothing more. */
  content_hmac: string | null;
  tool_id: string | null;
  created_at: string;
}

export interface DecisionFilter {
  stage?: DecisionStage;
  outcome?: string;
  agent_id?: string;
  user_id?: string;
  limit?: number;
}

/** Reads and writes in named places: what a "working alongside" grant allows. */
export interface GrantTarget {
  type: string;
  identifiers: string[];
  actions?: string[];
}

/**
 * A person let one agent on one of their devices run, for a while, what
 * would otherwise ask: one tool (`time`) or reads and writes in named places
 * (`work`). An administrator may end one; it holds from the agent's next call.
 */
export interface Grant {
  id: string;
  kind: "time" | "work";
  status: "active" | "paused";
  user_id: string;
  agent_id: string;
  agent_version: string | null;
  device_id: string | null;
  conversation_id: string | null;
  scope: { tools?: string[]; classes?: string[]; targets?: GrantTarget[] };
  issued_at: string;
  expires_at: string;
  paused_at: string | null;
  max_calls: number | null;
  calls_used: number;
  remaining_s: number;
}

/** List, create, change and remove one kind of ordered pattern rule. */
export interface RuleApi<Fields, Rule extends Fields & Stored> {
  list: () => Promise<Rule[]>;
  create: (fields: Fields) => Promise<Rule>;
  update: (id: string, fields: Fields) => Promise<Rule>;
  remove: (id: string) => Promise<void>;
}

function ruleApi<Fields, Rule extends Fields & Stored>(path: string): RuleApi<Fields, Rule> {
  return {
    list: async () => (await request<{ data: Rule[] }>(path)).data,
    create: (fields) => request<Rule>(path, json("POST", fields)),
    update: (id, fields) => request<Rule>(`${path}/${segment(id)}`, json("PUT", fields)),
    remove: (id) => request<void>(`${path}/${segment(id)}`, json("DELETE")),
  };
}

export const governanceApi = {
  toolRules: ruleApi<ToolRuleFields, ToolRule>("/tool-rules"),
  toolClasses: ruleApi<ToolClassFields, ToolClassRule>("/tool-classes"),
  decisions: async (filter: DecisionFilter = {}) =>
    (await request<{ data: PolicyDecision[] }>(`/decisions${query({ limit: 200, ...filter })}`)).data,
  grants: async () => (await request<{ data: Grant[] }>(`/grants${query({ tenant_id: "default" })}`)).data,
  revokeGrant: (id: string) => request<void>(`/grants/${segment(id)}`, json("DELETE")),
};
