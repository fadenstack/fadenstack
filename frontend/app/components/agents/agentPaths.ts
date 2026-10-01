/** Where an agent's console pages are. */
import type { Agent } from "~/api/agents";

export type AgentTab = "definition" | "versions" | "access" | "activity";

export const AGENTS_PATH = "/admin/agents";

export const agentPath = (agent: Pick<Agent, "slug">, tab?: AgentTab) =>
  `${AGENTS_PATH}/${encodeURIComponent(agent.slug)}${tab ? `?tab=${tab}` : ""}`;
