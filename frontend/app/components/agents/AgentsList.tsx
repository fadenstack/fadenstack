/**
 * Agents: the agents the user may see, what their users get (status, live
 * version) and the way in to each one. Those who may see every agent
 * (agents:read, agents:manage) get all of them; everyone else the ones they
 * manage -- the agents they merely use are the SDK's business.
 */
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";

import { agentRights, agentsApi, type Agent, type AgentStatus } from "~/api/agents";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { fmtAgo } from "~/components/dashboard/format";
import { PageHeader } from "~/components/dashboard/kit";
import { useAsyncData } from "~/lib/useAsyncData";
import { useCan } from "~/lib/useCan";

import { AgentStatusChip, AgentVersionChips } from "./AgentChips";
import { CreateAgentDialog } from "./CreateAgentDialog";
import { agentPath } from "./agentPaths";

const STATUSES: AgentStatus[] = ["draft", "published", "retired"];

export interface AgentsListProps {
  /** Where the agents pages are (`agentPaths`). */
  basePath: string;
  /** A way back, before the title (to the chat, on the users' pages). */
  back?: ReactNode;
}

export function AgentsList({ basePath, back }: AgentsListProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const rights = agentRights(useCan());
  const [status, setStatus] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const agents = useAsyncData(agentsApi.list, [], { initialValue: [] as Agent[] });
  const visible = rights.seeAll ? agents.data : agents.data.filter((a) => a.can_manage);
  const shown = status ? visible.filter((a) => a.status === status) : visible;

  const columns: ColumnDef<Agent>[] = [
    {
      key: "name",
      label: t("agents.name"),
      sortable: true,
      sortValue: (a) => a.name.toLowerCase(),
      searchValue: (a) => `${a.name} ${a.slug} ${a.description ?? ""}`,
      render: (a) => (
        <>
          <Typography fontWeight={600}>{a.name}</Typography>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {a.slug}
          </Typography>
        </>
      ),
      minWidth: 200,
    },
    {
      key: "description",
      label: t("agents.description"),
      render: (a) => (
        <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 360 }} noWrap title={a.description ?? ""}>
          {a.description || "—"}
        </Typography>
      ),
      minWidth: 200,
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (a) => STATUSES.indexOf(a.status),
      render: (a) => <AgentStatusChip status={a.status} />,
      minWidth: 110,
    },
    {
      key: "versions",
      label: t("agents.versions_column"),
      sortable: true,
      sortValue: (a) => a.latest_version ?? 0,
      render: (a) => <AgentVersionChips agent={a} />,
      minWidth: 180,
    },
    {
      key: "updated",
      label: t("agents.updated"),
      sortable: true,
      sortValue: (a) => a.updated_at,
      render: (a) => fmtAgo(a.updated_at, i18n.language),
      minWidth: 120,
    },
  ];

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        {back}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <PageHeader title={t("agents.title")} subtitle={t("agents.subtitle")} />
        </Box>
      </Stack>
      {agents.error && (
        <Alert severity="error" onClose={() => agents.setError(null)}>
          {agents.error}
        </Alert>
      )}
      <DataTable
        rows={shown}
        columns={columns}
        rowKey={(a) => a.id}
        loading={agents.loading}
        onRefresh={() => void agents.refresh()}
        onRowClick={(a) => navigate(agentPath(basePath, a))}
        emptyMessage={t(visible.length === 0 ? "agents.empty" : "agents.none_match")}
        searchPlaceholder={t("agents.search")}
        columnFilters={[
          {
            label: t("common.status"),
            value: status,
            onChange: setStatus,
            options: [
              { value: "", label: t("common.all") },
              ...STATUSES.map((s) => ({ value: s, label: t(`agents.status.${s}`) })),
            ],
            minWidth: 140,
          },
        ]}
        toolbarActions={
          rights.create && (
            <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
              {t("agents.create")}
            </Button>
          )
        }
        pagination={25}
      />
      <CreateAgentDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(agent) => {
          setCreateOpen(false);
          navigate(agentPath(basePath, agent, "definition"));
        }}
      />
    </Stack>
  );
}
