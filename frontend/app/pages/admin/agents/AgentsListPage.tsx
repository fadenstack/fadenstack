/** Agents, in the admin console. */
import { AgentsList } from "~/components/agents/AgentsList";
import { ADMIN_AGENTS_PATH } from "~/components/agents/agentPaths";

export default function AgentsListPage() {
  return <AgentsList basePath={ADMIN_AGENTS_PATH} />;
}
