/**
 * Local AI capacity: the machines, each as a card.
 *
 * This is the widget's small-fleet ("Cards") level; the cluster rows, health
 * vs. load colouring and semantic zoom for large fleets arrive with
 * /dashboard/capacity.
 */
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import NodeFleetRow from "../NodeFleetRow";
import { useNodes } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

export default function CapacityWidget({ refreshToken }: WidgetProps) {
  const { t } = useTranslation();
  const nodes = useNodes({ refreshToken });
  const refreshNode = useCallback((id: string) => nodesApi.get(id), []);
  const onRefreshNode = useCallback((_updated: ManagedNode) => void nodes.refresh(), [nodes]);

  return (
    <WidgetCard
      title={t("dashboard.widgets.capacity.title")}
      link={{ to: "/admin/nodes", label: t("dashboard.widgets.capacity.link") }}
      status={{
        loading: nodes.loading,
        error: nodes.error,
        hasData: Boolean(nodes.data),
        onRetry: nodes.refresh,
        stale: nodes.stale,
        updatedAt: nodes.updatedAt,
      }}
      skeleton={
        <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 1.5 }}>
          <Skeleton variant="rounded" height={180} />
          <Skeleton variant="rounded" height={180} />
        </Box>
      }
    >
      {nodes.data && <NodeFleetRow nodes={nodes.data} onRefreshNode={onRefreshNode} refreshNode={refreshNode} />}
    </WidgetCard>
  );
}
