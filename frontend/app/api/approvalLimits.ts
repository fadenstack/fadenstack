/**
 * Approval limits (`/api/ee/approval-limits`, for administrators): the longest grant and what grants record, for
 * everyone, a team, an agent or a person -- of those that apply to a grant the shortest longest grant and the most
 * recorded level win -- and the list of whom Bypass is for. Offered while the server lists the feature
 * {@link APPROVAL_LIMITS} (`GET /api/about`).
 */
import { json, requester, segment } from "./http";

const request = requester("/api/ee/approval-limits");

/** The feature the tab shows for. */
export const APPROVAL_LIMITS = "approval_limits";

export type LimitScope = "tenant" | "team" | "agent" | "user";
export type ListScope = "team" | "agent" | "user";
/** From the least recorded: the call; its arguments too; and its outcome and the start of its result. */
export type EvidenceLevel = "decisions" | "calls" | "results";
export const EVIDENCE_LEVELS: EvidenceLevel[] = ["decisions", "calls", "results"];
/** The longest grant an administrator may set, in minutes. */
export const LONGEST_MIN = 15;
export const LONGEST_MAX = 720;

export interface ApprovalLimit {
  scope: LimitScope;
  /** The team's, agent's or person's id; `default` for everyone. */
  target_id: string;
  label: string | null;
  longest_minutes: number | null;
  evidence_level: EvidenceLevel | null;
  updated_at: string;
  updated_by: string | null;
}

export interface BypassEntry {
  scope: ListScope;
  target_id: string;
  label: string | null;
  created_at: string;
  created_by: string | null;
}

/**
 * The two sides of the Bypass list, people (persons and teams) and agents, each restricted on its own: from its
 * first entry until an administrator lifts it. A restricted side with no entries names nobody, so Bypass is for
 * nobody on it -- also when its entries went with a deleted team, person or agent.
 */
export type BypassSide = "people" | "agents";
export interface BypassSides {
  people: boolean;
  agents: boolean;
}

export interface ApprovalLimits {
  limits: ApprovalLimit[];
  bypass_for: BypassEntry[];
  bypass_sides: BypassSides;
}

/** What a change of the Bypass list did: the sides now, the running Bypass sessions it ended, the entries it took. */
export interface BypassListChange {
  bypass_sides: BypassSides;
  ended: number;
  removed: number;
}

export interface LimitFields {
  longest_minutes: number | null;
  evidence_level: EvidenceLevel | null;
}

const at = (scope: string, target: string) => `/${segment(scope)}/${segment(target)}`;

export const approvalLimitsApi = {
  list: () => request<ApprovalLimits>(""),
  /** A shorter longest grant ends running grants in its scope at their start plus the limit; `cut` says how many. */
  set: (scope: LimitScope, target: string, fields: LimitFields) =>
    request<ApprovalLimit & { cut: number }>(`/limits${at(scope, target)}`, json("PUT", fields)),
  remove: (scope: LimitScope, target: string) => request<void>(`/limits${at(scope, target)}`, json("DELETE")),
  /** A side's first entry restricts it; the running Bypass sessions the list stops naming end (`ended`). */
  addToBypassList: (scope: ListScope, target: string) =>
    request<BypassEntry & { ended: number }>(`/bypass-for${at(scope, target)}`, json("PUT", {})),
  /** The side stays restricted; sessions the list stops naming end (`ended`). */
  removeFromBypassList: (scope: ListScope, target: string) =>
    request<BypassListChange>(`/bypass-for${at(scope, target)}`, json("DELETE")),
  /** Restrict a side with nobody named yet: Bypass for nobody on it; sessions it stops naming end. */
  restrictSide: (side: BypassSide) => request<BypassListChange>(`/bypass-sides/${segment(side)}`, json("PUT", {})),
  /** Lift a side: Bypass for any person (or agent) again, its entries go (`removed`). */
  liftSide: (side: BypassSide) => request<BypassListChange>(`/bypass-sides/${segment(side)}`, json("DELETE")),
};
