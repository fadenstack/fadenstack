/**
 * One machine as a card, for fleets small enough to show them whole.
 * Health is the machine's reported state; the bars are load, drawn in the
 * neutral colour -- a full GPU is not a fault.
 */
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import CardActionArea from "@mui/material/CardActionArea";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import type { CapacityMachine } from "~/api/dashboard";
import { fmtAgo, fmtPct } from "../format";
import { HEALTH_LABEL_KEYS, healthSx } from "./paint";

export default function MachineCard({ machine }: { machine: CapacityMachine }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const lang = i18n.language;

  return (
    <CardActionArea
      component={RouterLink}
      to={`/admin/nodes/${machine.id}`}
      sx={{ borderRadius: 1.5, border: 1, borderColor: "divider", bgcolor: "dashboard.surfaceSubtle", p: 1.5, height: "100%" }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 700, fontFamily: "monospace", flexGrow: 1, minWidth: 0 }} noWrap>
          {machine.name}
        </Typography>
        <Stack direction="row" alignItems="center" gap={0.5} sx={{ flexShrink: 0 }}>
          <Box aria-hidden sx={{ width: 10, height: 10, borderRadius: "50%", ...healthSx(machine.health, theme) }} />
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {t(HEALTH_LABEL_KEYS[machine.health])}
          </Typography>
        </Stack>
      </Stack>
      <Bar label={t("dashboard.capacity.cpu")} value={machine.cpu_pct} lang={lang} />
      <Bar label={t("dashboard.capacity.gpu")} value={machine.gpu_util_pct} lang={lang} />
      <Bar label={t("dashboard.capacity.vram_allocated")} value={machine.vram_allocated_pct} lang={lang} />
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }} noWrap>
        {machine.last_seen ? t("dashboard.capacity.seen", { ago: fmtAgo(machine.last_seen, lang) }) : t("dashboard.capacity.never_seen")}
        {machine.gpus.some((g) => g.temp_c != null)
          ? ` · ${machine.gpus
              .filter((g) => g.temp_c != null)
              .map((g) => `${Math.round(g.temp_c as number)} °C`)
              .join(" / ")}`
          : ""}
      </Typography>
    </CardActionArea>
  );
}

function Bar({ label, value, lang }: { label: string; value: number | null; lang: string }) {
  const theme = useTheme();
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "88px 1fr 44px", alignItems: "center", gap: 1, mb: 0.5 }}>
      <Typography variant="caption" color="text.secondary" noWrap>
        {label}
      </Typography>
      <Box sx={{ height: 6, borderRadius: 999, bgcolor: "action.hover", overflow: "hidden" }} aria-hidden>
        <Box
          sx={{
            width: `${Math.min(100, Math.max(0, value ?? 0))}%`,
            height: "100%",
            borderRadius: 999,
            bgcolor: theme.palette.dashboard.load[3],
          }}
        />
      </Box>
      <Typography variant="caption" sx={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {fmtPct(value, lang, 0)}
      </Typography>
    </Box>
  );
}
