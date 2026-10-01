/** How an agent is shown in lists and headers: its status and its versions. */
import { useTranslation } from "react-i18next";

import Chip, { type ChipProps } from "@mui/material/Chip";
import Stack from "@mui/material/Stack";

import type { Agent, AgentStatus } from "~/api/agents";

import { hasUnpublishedVersion } from "./agentDefinition";

const STATUS_COLOR: Record<AgentStatus, ChipProps["color"]> = {
  draft: "default",
  published: "success",
  retired: "warning",
};

export function AgentStatusChip({ status }: { status: AgentStatus }) {
  const { t } = useTranslation();
  return <Chip size="small" color={STATUS_COLOR[status]} label={t(`agents.status.${status}`)} />;
}

/** "v3 live" and, when a newer one waits, "v4 not published". */
export function AgentVersionChips({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {agent.published_version !== null && (
        <Chip size="small" variant="outlined" color="success" label={t("agents.version_live", { version: agent.published_version })} />
      )}
      {hasUnpublishedVersion(agent) && (
        <Chip size="small" variant="outlined" color="warning" label={t("agents.version_unpublished", { version: agent.latest_version })} />
      )}
    </Stack>
  );
}
