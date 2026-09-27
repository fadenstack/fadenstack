/**
 * One group of machines -- a cluster, or those in no cluster -- as a row.
 *
 * The row itself is the control: 44 px or more, a single tab stop, opening
 * the group's machine list. The cells (or the summary) under it are drawings,
 * hidden from assistive technology; a mouse can still hover a cell for its
 * machine and click through to it, which WCAG 2.5.8 allows because the list
 * gives every machine a full-size target.
 */
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import { MACHINE_HEALTH, type CapacityGroup, type CapacityMachine, type FleetColorBy } from "~/api/dashboard";
import { fmtAgo, fmtPct } from "../format";
import { HEALTH_LABEL_KEYS, cellSx, healthSx } from "./paint";
import { CELL_GAP_PX, CELL_PX, type Level } from "./zoom";

interface GroupRowProps {
  group: CapacityGroup;
  name: string;
  level: Exclude<Level, "cards">;
  colorBy: FleetColorBy;
  /** Open the machine list; absent for a row that has none (folded groups). */
  onOpen?: () => void;
}

export default function GroupRow({ group, name, level, colorBy, onOpen }: GroupRowProps) {
  const { t, i18n } = useTranslation();
  const problems = MACHINE_HEALTH.filter((h) => h !== "ok" && group.health_counts[h] > 0)
    .map((h) => `${t(HEALTH_LABEL_KEYS[h])}: ${group.health_counts[h]}`)
    .join(" · ");
  const gpus =
    group.gpus_total == null
      ? null
      : group.gpus_free != null
        ? t("dashboard.capacity.gpus_line", { free: group.gpus_free, total: group.gpus_total })
        : t("dashboard.capacity.gpus_total_line", { total: group.gpus_total });

  const header = (
    <Stack direction="row" alignItems="center" gap={1} sx={{ width: "100%", minHeight: 44, textAlign: "left", flexWrap: "wrap", rowGap: 0 }}>
      <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>
        {name}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
        {t("dashboard.capacity.online", { online: group.machines_online, total: group.machines_total })}
        {gpus ? ` · ${gpus}` : ""}
      </Typography>
      <Typography variant="caption" sx={{ fontWeight: 600, color: problems ? "text.primary" : "text.secondary" }}>
        {problems || t("dashboard.capacity.all_healthy")}
      </Typography>
      {group.stale && group.observed_at && (
        <Typography variant="caption" sx={{ color: "dashboard.health.degraded", fontWeight: 600 }}>
          {t("dashboard.capacity.as_of", { ago: fmtAgo(group.observed_at, i18n.language) })}
        </Typography>
      )}
      {onOpen && <ChevronRightIcon fontSize="small" sx={{ ml: "auto", color: "text.secondary" }} aria-hidden />}
    </Stack>
  );

  return (
    <Box component="li" sx={{ listStyle: "none" }}>
      {onOpen ? (
        <ButtonBase
          onClick={onOpen}
          aria-label={t("dashboard.capacity.group_aria", {
            name,
            online: group.machines_online,
            total: group.machines_total,
            problems: problems || t("dashboard.capacity.all_healthy"),
          })}
          sx={{ width: "100%", borderRadius: 1, px: 0.5, "&:hover": { bgcolor: "action.hover" }, "&.Mui-focusVisible": { outline: 2, outlineColor: "primary.main" } }}
        >
          {header}
        </ButtonBase>
      ) : (
        <Box sx={{ px: 0.5 }}>{header}</Box>
      )}
      <Box aria-hidden sx={{ px: 0.5, pb: 1 }}>
        {level === "cells" ? (
          <Cells machines={group.machines ?? []} colorBy={colorBy} />
        ) : (
          <Summary group={group} colorBy={colorBy} />
        )}
      </Box>
    </Box>
  );
}

function Cells({ machines, colorBy }: { machines: CapacityMachine[]; colorBy: FleetColorBy }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: `${CELL_GAP_PX}px` }}>
      {machines.map((m) => (
        <Tooltip
          key={m.id}
          disableTouchListener
          title={
            <>
              <b>{m.name}</b>
              <br />
              {t(HEALTH_LABEL_KEYS[m.health])} · {t("dashboard.capacity.gpu_short", { value: fmtPct(m.gpu_util_pct, i18n.language, 0) })} ·{" "}
              {t("dashboard.capacity.vram_short", { value: fmtPct(m.vram_allocated_pct, i18n.language, 0) })}
            </>
          }
        >
          <Box
            onClick={() => navigate(`/admin/nodes/${m.id}`)}
            sx={{
              width: CELL_PX,
              height: CELL_PX,
              borderRadius: "3px",
              boxSizing: "border-box",
              ...cellSx(m, colorBy, theme),
              // Only a mouse gets per-cell targets; touch uses the row.
              "@media (pointer: fine)": { cursor: "pointer", "&:hover": { transform: "scale(1.25)" } },
              transition: "transform 80ms ease",
            }}
          />
        </Tooltip>
      ))}
    </Box>
  );
}

function Summary({ group, colorBy }: { group: CapacityGroup; colorBy: FleetColorBy }) {
  const theme = useTheme();
  const { t } = useTranslation();
  const total = group.machines_total || 1;
  const histogram = colorBy === "vram_allocated" ? group.load_histogram.vram_allocated : group.load_histogram.gpu_util;
  const peak = Math.max(1, ...histogram);

  return (
    <Stack gap={0.75}>
      <Box sx={{ display: "flex", height: 10, borderRadius: 999, overflow: "hidden", bgcolor: "action.hover" }}>
        {MACHINE_HEALTH.filter((h) => group.health_counts[h] > 0).map((h) => (
          <Box key={h} sx={{ width: `${(group.health_counts[h] / total) * 100}%`, ...healthSx(h, theme), borderRadius: 0 }} />
        ))}
      </Box>
      <Stack direction="row" alignItems="flex-end" gap={1.5} sx={{ flexWrap: "wrap" }}>
        <Stack direction="row" alignItems="flex-end" gap="2px" sx={{ height: 22 }}>
          {histogram.map((count, band) => (
            <Box
              key={band}
              sx={{
                width: 10,
                height: `${Math.max(2, (count / peak) * 22)}px`,
                bgcolor: theme.palette.dashboard.load[band as 0 | 1 | 2 | 3 | 4],
                borderRadius: "2px",
              }}
            />
          ))}
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {t(colorBy === "vram_allocated" ? "dashboard.capacity.vram_legend" : "dashboard.capacity.load_legend")}
        </Typography>
        {group.exceptions.length > 0 && (
          <Typography variant="caption" sx={{ minWidth: 0 }} noWrap>
            {t("dashboard.capacity.needs_look", {
              names: group.exceptions
                .slice(0, 5)
                .map((m) => `${m.name} (${t(HEALTH_LABEL_KEYS[m.health])})`)
                .join(", "),
            })}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
