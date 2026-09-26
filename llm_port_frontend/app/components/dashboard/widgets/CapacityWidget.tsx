/**
 * Local AI capacity: clusters, machines and GPUs -- their health as reported,
 * their load kept apart from it -- from one machine to a thousand.
 *
 * Small fleets get a card per machine; larger ones a row per cluster with a
 * cell per machine; the largest a summary per cluster (spec §6.4.2). The
 * figures are the ones the agents and the reconciler stored; free GPUs of a
 * cluster never observed are unknown, not 0.
 */
import { useEffect, useRef, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import { MACHINE_HEALTH, type CapacityGroup, type CapacityResponse, type FleetColorBy } from "~/api/dashboard";
import { TOUCH_TARGET } from "../DashboardGrid";
import MachineCard from "../fleet/MachineCard";
import GroupRow from "../fleet/GroupRow";
import MachineList from "../fleet/MachineList";
import { HEALTH_LABEL_KEYS, healthSx } from "../fleet/paint";
import { fleetUsesCards, foldGroups, groupLevel } from "../fleet/zoom";
import { useCapacity } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard, { WidgetEmpty } from "../WidgetCard";

const COLOR_BY: FleetColorBy[] = ["health", "gpu_util", "vram_allocated"];

export default function CapacityWidget({ layout, onLayoutChange, refreshToken }: WidgetProps) {
  const { t } = useTranslation();
  const query = useCapacity({ refreshToken });
  const data = query.data;
  const colorBy = layout.fleet_color_by;

  return (
    <WidgetCard
      title={t("dashboard.widgets.capacity.title")}
      link={{ to: "/admin/nodes", label: t("dashboard.widgets.capacity.link") }}
      actions={
        <ToggleButtonGroup
          size="small"
          exclusive
          value={colorBy}
          onChange={(_, value: FleetColorBy | null) => value && onLayoutChange({ fleet_color_by: value })}
          aria-label={t("dashboard.capacity.color_by")}
        >
          {COLOR_BY.map((c) => (
            <ToggleButton key={c} value={c} sx={{ px: 1, py: 0.25, textTransform: "none", ...TOUCH_TARGET }}>
              {t(`dashboard.capacity.color_${c}`)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
      status={{
        loading: query.loading,
        error: query.error,
        hasData: Boolean(data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={
        <Stack gap={1.5}>
          <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 1 }}>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} variant="rounded" height={56} />
            ))}
          </Box>
          <Skeleton variant="rounded" height={90} />
        </Stack>
      }
      empty={
        data && data.totals.machines === 0 ? (
          <WidgetEmpty
            message={t("dashboard.capacity.empty")}
            action={
              <Button component={RouterLink} to="/admin/nodes?add=1" size="small" sx={{ minHeight: 44 }}>
                {t("dashboard.capacity.add_machine")}
              </Button>
            }
          />
        ) : undefined
      }
    >
      {data && <Fleet data={data} colorBy={colorBy} />}
    </WidgetCard>
  );
}

function Fleet({ data, colorBy }: { data: CapacityResponse; colorBy: FleetColorBy }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const [open, setOpen] = useState<{ group: CapacityGroup; name: string } | null>(null);
  const totals = data.totals;
  const nameOf = (g: CapacityGroup) =>
    g.kind === "unassigned" ? t("dashboard.capacity.unassigned") : (g.name ?? t("dashboard.traffic.removed_cluster"));
  const problems = (g: CapacityGroup) => g.machines_total - (g.health_counts.ok ?? 0);
  const { shown, folded } = foldGroups(data.groups, problems);

  return (
    <Stack gap={1.5} ref={ref}>
      <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 1 }}>
        <Count label={t("dashboard.capacity.clusters")} value={`${totals.clusters_healthy} / ${totals.clusters}`} />
        <Count label={t("dashboard.capacity.machines")} value={`${totals.machines_online} / ${totals.machines}`} />
        <Count label={t("dashboard.capacity.gpus")} value={String(totals.gpus)} />
        <Count
          label={t("dashboard.capacity.gpus_free")}
          value={totals.gpus_free == null ? "–" : `${totals.gpus_free_unknown_clusters ? "≥ " : ""}${totals.gpus_free}`}
          hint={totals.gpus_free_unknown_clusters ? t("dashboard.capacity.free_partial") : undefined}
        />
      </Box>

      <Legend colorBy={colorBy} />

      {fleetUsesCards(totals.machines) ? (
        <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 1.5 }}>
          {data.groups.flatMap((g) => g.machines ?? []).map((m) => (
            <MachineCard key={m.id} machine={m} />
          ))}
        </Box>
      ) : (
        <Stack component="ul" gap={0.5} sx={{ m: 0, p: 0 }}>
          {shown.map((g) => (
            <GroupRow
              key={g.id ?? "unassigned"}
              group={g}
              name={nameOf(g)}
              colorBy={colorBy}
              level={groupLevel(totals.machines, g.machines_total, width) === "cells" && g.machines ? "cells" : "summary"}
              onOpen={() => setOpen({ group: g, name: nameOf(g) })}
            />
          ))}
          {folded.length > 0 && (
            <GroupRow
              group={mergeGroups(folded)}
              name={t("dashboard.capacity.other_groups", { count: folded.length })}
              colorBy={colorBy}
              level="summary"
            />
          )}
        </Stack>
      )}

      <MachineList
        group={open?.group ?? null}
        title={open ? t("dashboard.capacity.list_title", { name: open.name }) : ""}
        onClose={() => setOpen(null)}
      />
    </Stack>
  );
}

function Count({ label, value, hint }: { label: string; value: string; hint?: string }) {
  const tile = (
    <Box sx={{ px: 1.25, py: 1, borderRadius: 1.5, border: 1, borderColor: "divider", bgcolor: "dashboard.surfaceSubtle" }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }} noWrap>
        {label}
      </Typography>
      <Typography sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</Typography>
    </Box>
  );
  return hint ? <Tooltip title={hint}>{tile}</Tooltip> : tile;
}

function Legend({ colorBy }: { colorBy: FleetColorBy }) {
  const { t } = useTranslation();
  const theme = useTheme();
  if (colorBy === "health") {
    return (
      <Stack direction="row" gap={1.5} sx={{ flexWrap: "wrap" }} aria-hidden>
        {MACHINE_HEALTH.map((h) => (
          <Stack key={h} direction="row" alignItems="center" gap={0.5}>
            <Box sx={{ width: 10, height: 10, borderRadius: "3px", boxSizing: "border-box", ...healthSx(h, theme) }} />
            <Typography variant="caption">{t(HEALTH_LABEL_KEYS[h])}</Typography>
          </Stack>
        ))}
      </Stack>
    );
  }
  return (
    <Stack direction="row" gap={1} alignItems="center" sx={{ flexWrap: "wrap" }} aria-hidden>
      <Typography variant="caption" color="text.secondary">
        {t(colorBy === "gpu_util" ? "dashboard.capacity.load_legend" : "dashboard.capacity.vram_legend")}
      </Typography>
      {theme.palette.dashboard.load.map((color, band) => (
        <Stack key={color} direction="row" alignItems="center" gap={0.5}>
          <Box sx={{ width: 10, height: 10, borderRadius: "3px", bgcolor: color, border: 1, borderColor: "divider" }} />
          <Typography variant="caption" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {`${band * 20}–${band * 20 + 20} %`}
          </Typography>
        </Stack>
      ))}
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Box sx={{ width: 10, height: 10, borderRadius: "3px", border: `1px dashed ${theme.palette.text.disabled}` }} />
        <Typography variant="caption">{t("dashboard.capacity.no_data")}</Typography>
      </Stack>
    </Stack>
  );
}

/** Folded groups as one summary row. */
function mergeGroups(groups: CapacityGroup[]): CapacityGroup {
  const sum = (pick: (g: CapacityGroup) => number) => groups.reduce((n, g) => n + pick(g), 0);
  const addLists = (lists: number[][]) => lists.reduce((acc, l) => acc.map((v, i) => v + (l[i] ?? 0)), [0, 0, 0, 0, 0]);
  return {
    kind: "cluster",
    id: null,
    name: null,
    status: null,
    observed_at: null,
    stale: false,
    machines_total: sum((g) => g.machines_total),
    machines_online: sum((g) => g.machines_online),
    gpus_total: sum((g) => g.gpus_total ?? 0),
    gpus_free: null,
    health_counts: Object.fromEntries(MACHINE_HEALTH.map((h) => [h, sum((g) => g.health_counts[h] ?? 0)])) as CapacityGroup["health_counts"],
    load_histogram: {
      gpu_util: addLists(groups.map((g) => g.load_histogram.gpu_util)),
      vram_allocated: addLists(groups.map((g) => g.load_histogram.vram_allocated)),
    },
    exceptions: groups.flatMap((g) => g.exceptions).slice(0, 5),
    machines: null,
    next_cursor: null,
  };
}

/** The element's width, kept current as it resizes. */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
