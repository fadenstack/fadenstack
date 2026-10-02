/**
 * The agents a user looks after, beside the chat: for maintainers and those
 * with an agents permission, who do not get the admin console.
 */
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import IconButton from "@mui/material/IconButton";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import { agentRights } from "~/api/agents";
import { UserPageFrame } from "~/components/UserPageFrame";
import { AgentsList } from "~/components/agents/AgentsList";
import { USER_AGENTS_PATH } from "~/components/agents/agentPaths";
import { useCan } from "~/lib/useCan";

export default function MyAgentsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const rights = agentRights(useCan());

  return (
    <UserPageFrame>
      {rights.open ? (
        <AgentsList
          basePath={USER_AGENTS_PATH}
          back={
            <IconButton onClick={() => navigate("/chat")} aria-label={t("agents.back_to_chat")}>
              <ArrowBackIcon />
            </IconButton>
          }
        />
      ) : (
        <Alert severity="warning">{t("agents.no_access")}</Alert>
      )}
    </UserPageFrame>
  );
}
