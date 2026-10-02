/** One agent a user looks after, beside the chat. The server decides what they may see and change. */
import { useParams } from "react-router";

import { UserPageFrame } from "~/components/UserPageFrame";
import { AgentDetail } from "~/components/agents/AgentDetail";
import { USER_AGENTS_PATH } from "~/components/agents/agentPaths";

export default function MyAgentPage() {
  const { slug = "" } = useParams<{ slug: string }>();
  return (
    <UserPageFrame>
      <AgentDetail slug={slug} basePath={USER_AGENTS_PATH} />
    </UserPageFrame>
  );
}
