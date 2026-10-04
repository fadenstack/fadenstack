/**
 * Where the agents pages are: in the admin console, and for everyone else
 * who looks after agents (maintainers, agents:create, agents:read) beside the
 * chat. The same pages serve both, under their own base path.
 */
import type { Agent } from "~/api/agents";

export type AgentTab = "definition" | "versions" | "apps" | "access" | "activity";

export const ADMIN_AGENTS_PATH = "/admin/agents";
export const USER_AGENTS_PATH = "/agents";

export const agentPath = (base: string, agent: Pick<Agent, "slug">, tab?: AgentTab) =>
  `${base}/${encodeURIComponent(agent.slug)}${tab ? `?tab=${tab}` : ""}`;
