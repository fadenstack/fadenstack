import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";
import NodeOnboardingDrawer from "./NodeOnboardingPage";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import BuildCircleIcon from "@mui/icons-material/BuildCircle";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import ExploreOffIcon from "@mui/icons-material/ExploreOff";
import Inventory2Icon from "@mui/icons-material/Inventory2";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

function statusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (
    status === "maintenance" ||
    status === "draining" ||
    status === "degraded"
  )
    return "warning";
  if (status === "offline" || status === "error") return "error";
  return "default";
}

function gpuCount(node: ManagedNode): number {
  const caps = node.capabilities ?? {};
  const fromFlat = Number(caps.gpu_count ?? 0);
  if (Number.isFinite(fromFlat) && fromFlat > 0) return fromFlat;
  const gpu = caps.gpu;
  if (gpu && typeof gpu === "object") {
    const fromNested = Number((gpu as Record<string, unknown>).count ?? 0);
    if (Number.isFinite(fromNested) && fromNested > 0) return fromNested;
  }
  return 0;
}

export default function NodeFleetPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data, loading, error, refresh, setError } = useAsyncData(
    () => nodesApi.list(),
    [],
    { initialValue: [] as ManagedNode[] },
  );
  const [actionBusyKey, setActionBusyKey] = useState<string | null>(null);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ManagedNode | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function runAction(key: string, action: () => Promise<void>) {
    setActionBusyKey(key);
    try {
      await action();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setActionBusyKey(null);
    }
  }

  const columns: ColumnDef<ManagedNode>[] = [
    {
      key: "host",
      label: t("nodes.host"),
      sortable: true,
      sortValue: (row) => row.host,
      searchValue: (row) => `${row.host} ${row.agent_id}`,
      render: (row) => (
        <Box>
          <Typography variant="body2" fontWeight={600}>
            {row.host}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            fontFamily="monospace"
          >
            {row.agent_id}
          </Typography>
        </Box>
      ),
    },
    {
      key: "status",
      label: t("nodes.status"),
      sortable: true,
      sortValue: (row) => row.status,
      searchValue: (row) => row.status,
      render: (row) => (
        <Chip size="small" color={statusColor(row.status)} label={row.status} />
      ),
    },
    {
      key: "gpu",
      label: t("nodes.gpu"),
      align: "center",
      sortable: true,
      sortValue: (row) => gpuCount(row),
      render: (row) => (
        <Typography variant="body2" fontFamily="monospace">
          {gpuCount(row)}
        </Typography>
      ),
    },
    {
      key: "flags",
      label: t("nodes.state"),
      searchValue: (row) =>
        `${row.maintenance_mode ? "maintenance" : ""} ${row.draining ? "draining" : ""}`.trim(),
      render: (row) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {row.maintenance_mode && (
            <Chip
              size="small"
              label="maintenance"
              color="warning"
              variant="outlined"
            />
          )}
          {row.draining && (
            <Chip
              size="small"
              label="draining"
              color="warning"
              variant="outlined"
            />
          )}
          {!row.maintenance_mode && !row.draining && (
            <Typography variant="body2" color="text.secondary">
              -
            </Typography>
          )}
        </Stack>
      ),
    },
    {
      key: "last_seen",
      label: t("nodes.last_seen"),
      sortable: true,
      sortValue: (row) => (row.last_seen ? Date.parse(row.last_seen) : 0),
      render: (row) => (
        <Typography variant="body2" color="text.secondary">
          {row.last_seen ? new Date(row.last_seen).toLocaleString() : "-"}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (row) => {
        const maintenanceKey = `${row.id}:maintenance`;
        const drainKey = `${row.id}:drain`;
        const invKey = `${row.id}:inventory`;
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            <Tooltip title={t("nodes.details")}>
              <IconButton
                size="small"
                onClick={(event) => {
                  event.stopPropagation();
                  navigate(`/admin/nodes/${row.id}`);
                }}
              >
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip
              title={
                row.maintenance_mode
                  ? t("nodes.disable_maintenance")
                  : t("nodes.enable_maintenance")
              }
            >
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(maintenanceKey, async () => {
                      await nodesApi.setMaintenance(
                        row.id,
                        !row.maintenance_mode,
                      );
                    });
                  }}
                  disabled={actionBusyKey === maintenanceKey}
                >
                  <BuildCircleIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip
              title={
                row.draining
                  ? t("nodes.disable_drain")
                  : t("nodes.enable_drain")
              }
            >
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(drainKey, async () => {
                      await nodesApi.setDrain(row.id, !row.draining);
                    });
                  }}
                  disabled={actionBusyKey === drainKey}
                >
                  <ExploreOffIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={t("nodes.refresh_inventory")}>
              <span>
                <IconButton
                  size="small"
                  onClick={(event) => {
                    event.stopPropagation();
                    runAction(invKey, async () => {
                      await nodesApi.issueCommand(row.id, {
                        command_type: "refresh_inventory",
                      });
                    });
                  }}
                  disabled={actionBusyKey === invKey}
                >
                  <Inventory2Icon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip
              title={t("nodes.delete_node", { defaultValue: "Delete node" })}
            >
              <span>
                <IconButton
                  size="small"
                  color="error"
                  onClick={(event) => {
                    event.stopPropagation();
                    setDeleteTarget(row);
                  }}
                >
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        );
      },
    },
  ];

  return (
    <>
      <DataTable
        title={t("nodes.fleet_title")}
        rows={data}
        columns={columns}
        rowKey={(row) => row.id}
        loading={loading}
        error={error}
        onRefresh={refresh}
        emptyMessage={t("nodes.no_nodes_registered")}
        searchPlaceholder={t("nodes.search_placeholder")}
        onRowClick={(row) => navigate(`/admin/nodes/${row.id}`)}
        toolbarActions={
          <Button variant="contained" onClick={() => setOnboardingOpen(true)}>
            {t("nodes.add_node")}
          </Button>
        }
        pagination={25}
        columnVisibilityKey="dt-node-fleet"
      />
      <NodeOnboardingDrawer
        open={onboardingOpen}
        onClose={() => setOnboardingOpen(false)}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("nodes.delete_node_title", { defaultValue: "Delete Node" })}
        message={t("nodes.delete_node_confirm", {
          defaultValue: `This will permanently remove the node "{{host}}" and all its sessions, inventory snapshots, commands, and events. This action cannot be undone.`,
          host: deleteTarget?.host,
        })}
        confirmText={deleteTarget?.host}
        confirmTextLabel={t("nodes.delete_node_type_host", {
          defaultValue: `Type "{{host}}" to confirm`,
          host: deleteTarget?.host,
        })}
        confirmLabel={t("common.delete", { defaultValue: "Delete" })}
        confirmColor="error"
        loading={deleting}
        onConfirm={async () => {
          if (!deleteTarget) return;
          setDeleting(true);
          try {
            await nodesApi.delete(deleteTarget.id);
            setDeleteTarget(null);
            await refresh();
          } catch (err: unknown) {
            setError(err instanceof Error ? err.message : "Delete failed.");
          } finally {
            setDeleting(false);
          }
        }}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
