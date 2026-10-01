/**
 * One agent: what it is (definition), its versions, who may use and maintain
 * it (access), and what it did (activity). The tab is in the address
 * (`?tab=`), so a link opens the right one; a tab, once opened, stays mounted
 * so an unsaved definition survives a look at another tab.
 */
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import EditIcon from "@mui/icons-material/Edit";
import RestoreFromTrashIcon from "@mui/icons-material/RestoreFromTrash";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";

import { agentsApi, type Agent, type AgentVersion } from "~/api/agents";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { AgentAccessPanel } from "~/components/agents/AgentAccessPanel";
import { AgentActivityPanel } from "~/components/agents/AgentActivityPanel";
import { AgentStatusChip, AgentVersionChips } from "~/components/agents/AgentChips";
import { AgentDefinitionPanel } from "~/components/agents/AgentDefinitionPanel";
import { AgentVersionsPanel } from "~/components/agents/AgentVersionsPanel";
import { EditAgentDialog } from "~/components/agents/EditAgentDialog";
import { AGENTS_PATH, type AgentTab } from "~/components/agents/agentPaths";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";
import { useTabParam } from "~/lib/useTabParam";

const TABS: AgentTab[] = ["definition", "versions", "access", "activity"];

export default function AgentDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { slug = "" } = useParams<{ slug: string }>();
  const [tab, setTab] = useTabParam(TABS);
  const [opened, setOpened] = useState<Set<AgentTab>>(() => new Set([tab]));

  const agentQuery = useAsyncData(() => agentsApi.get(slug), [slug], { initialValue: null as Agent | null });
  const versions = useAsyncData(() => agentsApi.versions(slug), [slug], { initialValue: [] as AgentVersion[] });
  const agent = agentQuery.data;

  /** After a change: the agent and its versions as the server has them now. */
  const reload = async () => {
    await Promise.all([agentQuery.refresh(true), versions.refresh(true)]);
  };

  function openTab(next: AgentTab) {
    setOpened((prev) => new Set(prev).add(next));
    setTab(next);
  }

  const back = (
    <IconButton aria-label={t("common.back")} onClick={() => navigate(AGENTS_PATH)}>
      <ArrowBackIcon />
    </IconButton>
  );

  if (!agent) {
    return (
      <Stack spacing={2}>
        <Stack direction="row" alignItems="center" spacing={1}>
          {back}
          {agentQuery.loading && <Skeleton width={240} height={40} />}
        </Stack>
        {agentQuery.error && <Alert severity="error">{agentQuery.error}</Alert>}
      </Stack>
    );
  }

  const panel = (name: AgentTab) => {
    switch (name) {
      case "definition":
        return versions.loading && versions.data.length === 0 ? (
          <Skeleton variant="rounded" height={320} />
        ) : (
          <AgentDefinitionPanel agent={agent} latest={versions.data[0] ?? null} onSaved={() => void reload()} />
        );
      case "versions":
        return (
          <AgentVersionsPanel
            agent={agent}
            versions={versions.data}
            loading={versions.loading}
            onChanged={() => void reload()}
            onRefresh={() => void versions.refresh()}
          />
        );
      case "access":
        return <AgentAccessPanel agent={agent} />;
      case "activity":
        return <AgentActivityPanel agent={agent} />;
    }
  };

  return (
    <Stack spacing={2}>
      <AgentHeader agent={agent} back={back} onChanged={() => void reload()} />
      {versions.error && <Alert severity="error">{versions.error}</Alert>}
      <Tabs
        value={tab}
        onChange={(_, next: AgentTab) => openTab(next)}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: "divider" }}
      >
        {TABS.map((name) => (
          <Tab key={name} value={name} label={t(`agents.tab.${name}`)} />
        ))}
      </Tabs>
      {TABS.filter((name) => opened.has(name) || name === tab).map((name) => (
        <Box key={name} role="tabpanel" hidden={name !== tab}>
          {panel(name)}
        </Box>
      ))}
    </Stack>
  );
}

/** Name, address, status and versions; edit, retire and restore for those who manage it. */
function AgentHeader({ agent, back, onChanged }: { agent: Agent; back: React.ReactNode; onChanged: () => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const change = useAction();
  const retired = agent.status === "retired";

  return (
    <>
      <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "flex-start" }}>
        <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ flex: 1, minWidth: 0 }}>
          {back}
          <Box sx={{ minWidth: 0 }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Typography variant="h5" component="h1" noWrap>
                {agent.name}
              </Typography>
              <AgentStatusChip status={agent.status} />
              <AgentVersionChips agent={agent} />
            </Stack>
            <Typography variant="body2" color="text.secondary" fontFamily="monospace">
              {agent.slug}
            </Typography>
            {agent.description && (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                {agent.description}
              </Typography>
            )}
          </Box>
        </Stack>
        {agent.can_manage && (
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            <Button size="small" startIcon={<EditIcon />} onClick={() => setEditing(true)}>
              {t("common.edit")}
            </Button>
            {retired ? (
              <Button
                size="small"
                variant="outlined"
                startIcon={<RestoreFromTrashIcon />}
                disabled={change.busy}
                onClick={() => void change.run(() => agentsApi.restore(agent.slug), onChanged)}
              >
                {t("agents.restore")}
              </Button>
            ) : (
              <Button
                size="small"
                color="error"
                variant="outlined"
                startIcon={<RemoveCircleOutlineIcon />}
                onClick={() => setRetiring(true)}
              >
                {t("agents.retire")}
              </Button>
            )}
          </Stack>
        )}
      </Stack>
      {change.error && (
        <Alert severity="error" onClose={() => change.setError(null)}>
          {change.error}
        </Alert>
      )}

      <EditAgentDialog
        agent={agent}
        open={editing}
        onClose={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          onChanged();
        }}
      />
      <ConfirmDialog
        open={retiring}
        title={t("agents.retire_title", { name: agent.name })}
        message={t("agents.retire_confirm")}
        confirmLabel={t("agents.retire")}
        cancelLabel={t("common.cancel")}
        loading={change.busy}
        onClose={() => setRetiring(false)}
        onConfirm={() =>
          void change.run(
            () => agentsApi.retire(agent.slug),
            () => {
              setRetiring(false);
              onChanged();
            },
          )
        }
      />
    </>
  );
}
