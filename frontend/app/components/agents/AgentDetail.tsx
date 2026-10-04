/**
 * One agent: what it is (definition), its versions, who may use and maintain
 * it (access), and what it did (activity). The tab is in the address
 * (`?tab=`), so a link opens the right one; a tab, once opened, stays mounted
 * so an unsaved definition survives a look at another tab.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DeleteForeverIcon from "@mui/icons-material/DeleteForever";
import EditIcon from "@mui/icons-material/Edit";
import RestoreFromTrashIcon from "@mui/icons-material/RestoreFromTrash";
import RemoveCircleOutlineIcon from "@mui/icons-material/RemoveCircleOutline";

import { agentRights, agentsApi, type Agent, type AgentVersion, type AppsReview } from "~/api/agents";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";
import { useCan } from "~/lib/useCan";
import { useTabParam } from "~/lib/useTabParam";

import { AgentAccessPanel } from "./AgentAccessPanel";
import { AgentActivityPanel } from "./AgentActivityPanel";
import { AgentAppsPanel } from "./AgentAppsPanel";
import { AgentStatusChip, AgentVersionChips } from "./AgentChips";
import { AgentDefinitionPanel } from "./AgentDefinitionPanel";
import { AgentVersionsPanel } from "./AgentVersionsPanel";
import { EditAgentDialog } from "./EditAgentDialog";
import type { AgentTab } from "./agentPaths";

const TABS: AgentTab[] = ["definition", "versions", "apps", "access", "activity"];

export interface AgentDetailProps {
  slug: string;
  /** Where the agents pages are (`agentPaths`): the way back. */
  basePath: string;
}

export function AgentDetail({ slug, basePath }: AgentDetailProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [tab, setTab] = useTabParam(TABS);
  const [opened, setOpened] = useState<Set<AgentTab>>(() => new Set([tab]));

  const agentQuery = useAsyncData(() => agentsApi.get(slug), [slug], { initialValue: null as Agent | null });
  const versions = useAsyncData(() => agentsApi.versions(slug), [slug], { initialValue: [] as AgentVersion[] });
  // What the apps bring: the tab's badge counts what the gateway still holds back.
  const apps = useAsyncData(() => agentsApi.apps(slug), [slug], { initialValue: null as AppsReview | null });
  const agent = agentQuery.data;

  /** After a change: the agent, its versions and what the apps bring, as the server has them now. */
  const reload = async () => {
    await Promise.all([agentQuery.refresh(true), versions.refresh(true), apps.refresh(true)]);
  };

  function openTab(next: AgentTab) {
    setOpened((prev) => new Set(prev).add(next));
    setTab(next);
  }

  const back = (
    <IconButton aria-label={t("common.back")} onClick={() => navigate(basePath)}>
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
      case "apps":
        return (
          <AgentAppsPanel
            agent={agent}
            review={apps.data}
            loading={apps.loading}
            error={apps.error}
            onRefresh={() => void apps.refresh()}
            onChanged={() => void reload()}
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
      <AgentHeader agent={agent} back={back} onChanged={() => void reload()} onDeleted={() => navigate(basePath)} />
      {versions.error && <Alert severity="error">{versions.error}</Alert>}
      <Tabs
        value={tab}
        onChange={(_, next: AgentTab) => openTab(next)}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: "divider" }}
      >
        {TABS.map((name) => (
          <Tab
            key={name}
            value={name}
            label={
              name === "apps" && apps.data && apps.data.live_pending > 0 ? (
                <Stack direction="row" spacing={0.75} alignItems="center">
                  <span>{t(`agents.tab.${name}`)}</span>
                  <Chip
                    size="small"
                    color="warning"
                    label={apps.data.live_pending}
                    aria-label={t("agents.apps.badge", { count: apps.data.live_pending })}
                  />
                </Stack>
              ) : (
                t(`agents.tab.${name}`)
              )
            }
          />
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

/**
 * Name, address, status and versions; edit, retire and restore for those who manage it. Deleting a retired agent
 * is a role's right (`agents:delete`), not its maintainers'.
 */
function AgentHeader({
  agent,
  back,
  onChanged,
  onDeleted,
}: {
  agent: Agent;
  back: React.ReactNode;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const rights = agentRights(useCan());
  const [editing, setEditing] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const change = useAction();
  const retired = agent.status === "retired";
  const canDelete = retired && rights.delete;

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
        {(agent.can_manage || canDelete) && (
          <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
            {agent.can_manage && (
              <Button size="small" startIcon={<EditIcon />} onClick={() => setEditing(true)}>
                {t("common.edit")}
              </Button>
            )}
            {canDelete && (
              <Button
                size="small"
                color="error"
                variant="outlined"
                startIcon={<DeleteForeverIcon />}
                onClick={() => setDeleting(true)}
              >
                {t("agents.delete")}
              </Button>
            )}
            {!agent.can_manage ? null : retired ? (
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
        open={deleting}
        title={t("agents.delete_title", { name: agent.name })}
        message={t("agents.delete_confirm")}
        confirmLabel={t("agents.delete")}
        cancelLabel={t("common.cancel")}
        confirmText={agent.slug}
        confirmTextLabel={t("agents.delete_type", { slug: agent.slug })}
        loading={change.busy}
        onClose={() => setDeleting(false)}
        onConfirm={() =>
          void change.run(
            () => agentsApi.remove(agent.slug),
            () => {
              setDeleting(false);
              onDeleted();
            },
          )
        }
      />
      <ConfirmDialog
        open={retiring}
        title={t("agents.retire_title", { name: agent.name })}
        message={rights.delete ? `${t("agents.retire_confirm")} ${t("agents.retire_then_delete")}` : t("agents.retire_confirm")}
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
