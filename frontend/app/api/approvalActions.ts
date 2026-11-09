/**
 * Agent actions (`/api/ee/approvals/actions`): every agent action with its approval state, the turn and the versions
 * in effect, for holders of the role `approvals_auditor` only; each read and each export is audited. Offered while
 * the server lists the feature {@link APPROVAL_VIEWS} (`GET /api/about`).
 */
import { query, requester } from "./http";

const request = requester("/api/ee/approvals");
/** Where the export is fetched from: a file download, as the browser follows the link. */
export const EXPORT_PATH = "/api/ee/approvals/actions/export";

/** The feature the tab shows for. */
export const APPROVAL_VIEWS = "approval_views";

/**
 * What happened to the call: the gateway asked and the person answered (`once`, `chat`, `always`, `approved` from
 * a client that names no scope), declined or let it expire; a rule refused it (`blocked`); a grant or Bypass
 * covered it; it ran without a question (`auto`); the device reported running it (`ran`).
 */
export type ActionState =
  | "asked"
  | "once"
  | "chat"
  | "always"
  | "approved"
  | "declined"
  | "expired"
  | "blocked"
  | "grant"
  | "bypass"
  | "auto"
  | "ran";

export interface AgentAction {
  /** Unique across the sources: `decision:…`, `grant-call:…`, `event:…`, `tool-call:…`. */
  key: string;
  at: string;
  /** Whose record it is: the gateway's own, or the device's report. */
  source: "gateway" | "device";
  state: ActionState;
  tool: string | null;
  side: "client" | "gateway" | null;
  user_id: string | null;
  person: string | null;
  agent_id: string | null;
  agent: string | null;
  agent_version: number | null;
  /** The digest of the agent version's instructions. */
  prompt: string | null;
  device_id: string | null;
  conversation_id: string | null;
  turn_id: string | null;
  request_id: string | null;
  model: string | null;
  grant_id: string | null;
  grant_kind: "time" | "work" | "bypass" | null;
  policy: string | null;
  outcome: string | null;
  /** With a workspace search: the place the call acted on. */
  place?: string | null;
}

export interface ActionFilter {
  person?: string | null;
  agent?: string | null;
  device?: string | null;
  workspace?: string | null;
  /** ISO 8601 with offset; at most 31 days apart; the last day by default. */
  since?: string | null;
  until?: string | null;
}

export interface ActionPage {
  data: AgentAction[];
  next: { before_at: string; before_key: string } | null;
  since: string;
  until: string;
  places_truncated: boolean;
}

export const approvalActionsApi = {
  list: (filter: ActionFilter = {}, cursor: ActionPage["next"] = null, limit = 100) =>
    request<ActionPage>(`/actions${query({ ...filter, limit, ...(cursor ?? {}) })}`),
  /** The export's address for the same filter (CloudEvents, one per line). */
  exportUrl: (filter: ActionFilter = {}) => `${EXPORT_PATH}${query({ ...filter })}`,
};
