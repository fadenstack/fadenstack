/**
 * An agent's versions, newest first: which one its users get, publishing
 * another (an older one rolls back), what changed, and each one as it was.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CompareArrowsIcon from "@mui/icons-material/CompareArrows";
import PublishIcon from "@mui/icons-material/Publish";
import RestoreIcon from "@mui/icons-material/Restore";
import VisibilityIcon from "@mui/icons-material/Visibility";

import { agentsApi, type Agent, type AgentVersion } from "~/api/agents";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { formatDateTime } from "~/lib/dates";
import { useAction } from "~/lib/useAction";

import { AgentDefinitionEditor } from "./AgentDefinitionEditor";
import { definitionOf } from "./agentDefinition";
import { VersionComparison } from "./VersionComparison";

type Shown = { kind: "view"; version: AgentVersion } | { kind: "compare"; before: AgentVersion; after: AgentVersion };

export interface AgentVersionsPanelProps {
  agent: Agent;
  /** Newest first. */
  versions: AgentVersion[];
  loading: boolean;
  onChanged: (agent: Agent) => void;
  onRefresh: () => void;
}

export function AgentVersionsPanel({ agent, versions, loading, onChanged, onRefresh }: AgentVersionsPanelProps) {
  const { t } = useTranslation();
  const [shown, setShown] = useState<Shown | null>(null);
  const [publishing, setPublishing] = useState<AgentVersion | null>(null);
  const publish = useAction();
  const live = agent.published_version;
  const canPublish = agent.can_manage && agent.status !== "retired";

  const previous = (version: AgentVersion) => versions.find((v) => v.version < version.version);

  const columns: ColumnDef<AgentVersion>[] = [
    {
      key: "version",
      label: t("agents.versions.version"),
      sortable: true,
      sortValue: (v) => v.version,
      render: (v) => (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography fontWeight={600}>v{v.version}</Typography>
          {v.version === live && <Chip size="small" color="success" label={t("agents.versions.live")} />}
          {v.version === agent.latest_version && v.version !== live && (
            <Chip size="small" variant="outlined" label={t("agents.versions.latest")} />
          )}
        </Stack>
      ),
      minWidth: 180,
    },
    {
      key: "created",
      label: t("agents.versions.created"),
      sortable: true,
      sortValue: (v) => v.created_at,
      render: (v) => formatDateTime(v.created_at),
      minWidth: 180,
    },
    {
      key: "published",
      label: t("agents.versions.published"),
      render: (v) => formatDateTime(v.published_at, t("agents.versions.never_published")),
      minWidth: 180,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (v) => {
        const before = previous(v);
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            <Button size="small" startIcon={<VisibilityIcon />} onClick={() => setShown({ kind: "view", version: v })}>
              {t("agents.versions.view")}
            </Button>
            {before && (
              <Button
                size="small"
                startIcon={<CompareArrowsIcon />}
                onClick={() => setShown({ kind: "compare", before, after: v })}
              >
                {t("agents.versions.changes")}
              </Button>
            )}
            {canPublish && v.version !== live && (
              <Button
                size="small"
                variant="outlined"
                startIcon={live !== null && v.version < live ? <RestoreIcon /> : <PublishIcon />}
                onClick={() => setPublishing(v)}
              >
                {live !== null && v.version < live ? t("agents.versions.roll_back") : t("agents.publish")}
              </Button>
            )}
          </Stack>
        );
      },
      minWidth: 320,
    },
  ];

  return (
    <>
      {publish.error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => publish.setError(null)}>
          {publish.error}
        </Alert>
      )}
      {agent.status === "retired" && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t("agents.versions.retired_hint")}
        </Alert>
      )}
      <DataTable
        rows={versions}
        columns={columns}
        rowKey={(v) => v.id}
        loading={loading}
        onRefresh={onRefresh}
        emptyMessage={t("agents.versions.empty")}
      />

      <Dialog open={shown !== null} onClose={() => setShown(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          {shown?.kind === "view"
            ? t("agents.versions.view_title", { version: shown.version.version })
            : shown && t("agents.versions.changes_title", { from: shown.before.version, to: shown.after.version })}
        </DialogTitle>
        <DialogContent dividers>
          {shown?.kind === "view" && (
            <AgentDefinitionEditor value={definitionOf(shown.version)} onChange={() => undefined} readOnly />
          )}
          {shown?.kind === "compare" && (
            <VersionComparison before={definitionOf(shown.before)} after={definitionOf(shown.after)} />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShown(null)}>{t("common.close")}</Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={publishing !== null}
        title={t("agents.versions.publish_title", { version: publishing?.version })}
        message={t(
          live !== null && publishing && publishing.version < live
            ? "agents.versions.roll_back_confirm"
            : "agents.versions.publish_confirm",
          { version: publishing?.version, name: agent.name },
        )}
        confirmLabel={t("agents.publish")}
        confirmColor="primary"
        loading={publish.busy}
        onClose={() => setPublishing(null)}
        onConfirm={() =>
          void publish.run(
            () => agentsApi.publish(agent.slug, publishing!.version),
            (updated) => {
              setPublishing(null);
              onChanged(updated);
            },
          )
        }
      />
    </>
  );
}
