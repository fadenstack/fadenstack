/**
 * Writing teams with their place in the tree (`/api/admin/groups`): name, description and roles as
 * before, plus the parent and the cost centre; archiving and bringing back.
 *
 * Through the shared requester, so a refusal keeps its code: the backend checks every change and answers
 * 422 `{"detail": {"code": "team_cycle" | "team_too_deep" | "team_archived"}}`, which the console words
 * (`groups.errors.<code>`). A team kept by a sign-in provider can still get a parent and a cost centre:
 * the tree is the organisation's, the provider names the team and its members.
 */
import type { Group } from "./admin";
import { ApiError, errorText, json, requester, segment } from "./http";

const request = requester("/api/admin/groups");

export interface TeamWrite {
  name?: string;
  description?: string;
  role_ids?: string[];
  /** null: a top team. */
  parent_id?: string | null;
  /** null or empty: none. */
  cost_center?: string | null;
}

/** The refusals of a change to the tree. */
export const TEAM_ERROR_CODES = ["team_cycle", "team_too_deep", "team_archived"] as const;

export type TeamErrorCode = (typeof TEAM_ERROR_CODES)[number];

/** The longest cost centre the backend keeps. */
export const COST_CENTER_MAX = 64;

export const teamAdmin = {
  create(body: TeamWrite) {
    return request<Group>("/", json("POST", body));
  },
  update(groupId: string, body: TeamWrite) {
    return request<Group>(`/${segment(groupId)}`, json("PATCH", body));
  },
  /** The team keeps its history; it gets no new members, cannot be a home team and leaves the pickers. */
  archive(groupId: string) {
    return request<Group>(`/${segment(groupId)}/archive`, json("POST"));
  },
  unarchive(groupId: string) {
    return request<Group>(`/${segment(groupId)}/unarchive`, json("POST"));
  },
};

/** A refusal of the tree in words (`groups.errors.<code>`), else the server's message. */
export function teamErrorText(err: unknown, t: (key: string) => string): string {
  if (err instanceof ApiError && (TEAM_ERROR_CODES as readonly string[]).includes(err.code ?? "")) {
    return t(`groups.errors.${err.code}`);
  }
  return errorText(err);
}
