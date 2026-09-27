/** The Grafana overview dashboard, embedded. Lazy: the iframe loads only when shown. */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";
import { grafanaOverviewUrl } from "~/lib/serviceUrls";
import type { WidgetProps } from "../types";
import WidgetCard from "../WidgetCard";

export default function GrafanaWidget(_props: WidgetProps) {
  const { t } = useTranslation();
  const theme = useTheme();
  const url = useMemo(
    () => grafanaOverviewUrl(theme.palette.mode === "dark" ? "dark" : "light"),
    [theme.palette.mode],
  );

  return (
    <WidgetCard title={t("dashboard.widgets.grafana.title")} link={{ to: url, label: t("dashboard.open_full"), external: true }}>
      <Box
        component="iframe"
        loading="lazy"
        src={`${url}${url.includes("?") ? "&" : "?"}kiosk`}
        title={t("dashboard.grafana_title")}
        sx={{
          display: "block",
          width: "100%",
          height: { xs: 300, md: 720 },
          border: 0,
          borderRadius: 1,
          bgcolor: "background.default",
        }}
      />
    </WidgetCard>
  );
}
