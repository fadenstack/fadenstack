/**
 * People excluded from an agent (`/api/ee/agents/{agent}/exclusions`): they may not use it, even through a
 * team that may. Offered while the server lists the feature {@link AGENT_EXCLUSIONS} (`GET /api/about`), to
 * those who manage the agent. Maintainers and administrators always keep an agent and cannot be excluded.
 */
import { json, requester, segment } from "./http";

const request = requester("/api/ee/agents");

/** The feature the screens show for. */
export const AGENT_EXCLUSIONS = "agent_exclusions";

export interface AgentExclusion {
  user_id: string;
  email: string;
  /** Why, for the managers; the person refused is told only that they are excluded. */
  reason: string;
  created_at: string;
  /** Who excluded them (their email); null once that account is gone. */
  created_by: string | null;
}

const at = (agent: string) => `/${segment(agent)}/exclusions`;

export const agentExclusionsApi = {
  list: (agent: string) => request<AgentExclusion[]>(at(agent)),
  /** Exclude a person (again: with the new reason). */
  add: (agent: string, userId: string, reason: string) =>
    request<AgentExclusion>(at(agent), json("POST", { user_id: userId, reason })),
  /** Let the person use the agent again, through their grants. */
  remove: (agent: string, userId: string) => request<void>(`${at(agent)}/${segment(userId)}`, json("DELETE")),
};
