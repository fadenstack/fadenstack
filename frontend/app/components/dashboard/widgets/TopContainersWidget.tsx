/** Top containers by CPU and by memory on this server (Docker stats, ~2 s to sample). */
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import type { DashboardTopUser } from "~/api/admin";
import { fmtBytes, fmtPct } from "../format";
import { useTopContainers } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

export default function TopContainersWidget({ refreshToken }: WidgetProps) {
  const { t, i18n } = useTranslation();
  const query = useTopContainers({ refreshToken });
  const data = query.data;

  return (
    <WidgetCard
      title={t("dashboard.widgets.top_containers.title")}
      link={{ to: "/admin/containers", label: t("dashboard.widgets.top_containers.link") }}
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={<Skeleton variant="rounded" height={160} />}
    >
      {data && (
        <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 2 }}>
          <Ranked
            title={t("dashboard.top_cpu")}
            items={data.top_cpu_containers}
            format={(v) => fmtPct(v, i18n.language, 2)}
          />
          <Ranked
            title={t("dashboard.top_memory")}
            items={data.top_memory_containers}
            format={(v) => fmtBytes(v, i18n.language)}
          />
        </Box>
      )}
    </WidgetCard>
  );
}

function Ranked({
  title,
  items,
  format,
}: {
  title: string;
  items: DashboardTopUser[];
  format: (value: number) => string;
}) {
  const { t } = useTranslation();
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {items.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("dashboard.no_running_stats")}
        </Typography>
      ) : (
        <List dense disablePadding>
          {items.map((item) => (
            <ListItem key={item.container_id} disableGutters>
              <ListItemText
                primary={item.name}
                secondary={format(item.value)}
                slotProps={{ primary: { sx: { fontFamily: "monospace", fontSize: "0.85rem" }, noWrap: true } }}
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
}
