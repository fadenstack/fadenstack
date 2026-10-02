/** One agent, in the admin console. */
import { useParams } from "react-router";

import { AgentDetail } from "~/components/agents/AgentDetail";
import { ADMIN_AGENTS_PATH } from "~/components/agents/agentPaths";

export default function AgentDetailPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  return <AgentDetail slug={slug} basePath={ADMIN_AGENTS_PATH} />;
}
