/**
 * A newer agent is available for this machine: said, never done. Updating is
 * the operator's step, in a window they pick -- re-running this server's
 * install line on the machine -- so a production load is never interrupted
 * by an agent replacing itself.
 */
import { useTranslation } from "react-i18next";

import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import SystemUpdateAltIcon from "@mui/icons-material/SystemUpdateAlt";

export default function AgentUpdateChip({ version }: { version?: string | null }) {
  const { t } = useTranslation();
  if (!version) return null;
  return (
    <Tooltip title={t("nodes.agent_update_hint")}>
      <Chip
        size="small"
        color="info"
        variant="outlined"
        icon={<SystemUpdateAltIcon />}
        label={t("nodes.agent_update", { version })}
        data-testid="agent-update"
      />
    </Tooltip>
  );
}
