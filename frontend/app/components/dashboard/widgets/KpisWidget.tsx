/**
 * Key metrics: a strip of 4–6 tiles the user picks. Each shows its value, a
 * trend against a stated baseline, and one line of context.
 *
 * A trend's colour says good or bad, not up or down: more errors is bad, more
 * requests is neither. With no baseline (a fresh install) there is no trend.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import TuneIcon from "@mui/icons-material/Tune";
import type { KpiItem, Money } from "~/api/dashboard";
import { KPI_IDS, MAX_KPIS, type KpiId } from "~/lib/dashboardLayoutModel";
import { TOUCH_TARGET } from "../DashboardGrid";
import { fmtChange, fmtCompact, fmtMoney, fmtMs, fmtShare } from "../format";
import { useKpis } from "../queries";
import type { WidgetProps } from "../types";
import WidgetCard, { WidgetEmpty } from "../WidgetCard";
import StatTile, { StatGrid, type TrendInfo } from "../StatTile";

type Kind = "count" | "share" | "money";
/** Which direction is good: "up" (in-house share), "down" (errors), or neither. */
type Better = "up" | "down" | "neutral";

interface KpiMeta {
  kind: Kind;
  better: Better;
  context: (item: KpiItem, t: (k: string, o?: Record<string, unknown>) => string, lang: string) => string | null;
}

const META: Partial<Record<KpiId, KpiMeta>> = {
  requests_today: {
    kind: "count",
    better: "neutral",
    context: (i, t, lang) => t("dashboard.kpi.requests_today.context", { tokens: fmtCompact(i.context.tokens, lang) }),
  },
  active_users_today: {
    kind: "count",
    better: "up",
    context: (i, t, lang) => t("dashboard.kpi.active_users_today.context", { users: fmtCompact(i.context.month, lang) }),
  },
  in_house_share_today: {
    kind: "share",
    better: "up",
    context: (i, t, lang) =>
      i.context.routed
        ? t("dashboard.kpi.in_house_share_today.context", {
            external: fmtShare(i.context.external, lang, 0),
            unknown: fmtShare(i.context.unknown, lang, 0),
          })
        : t("dashboard.kpi.in_house_share_today.no_traffic"),
  },
  spend_month: {
    kind: "money",
    better: "neutral",
    context: (i, t) =>
      i.context.unpriced_requests
        ? t("dashboard.kpi.spend_month.unpriced", { count: i.context.unpriced_requests })
        : t("dashboard.kpi.spend_month.context"),
  },
  error_rate_1h: {
    kind: "share",
    better: "down",
    context: (i, t, lang) => t("dashboard.kpi.error_rate_1h.context", { p95: fmtMs(i.context.p95_ms, lang) }),
  },
  attention_open: {
    kind: "count",
    better: "down",
    context: (i, t) =>
      t("dashboard.kpi.attention_open.context", { critical: i.context.critical ?? 0, warning: i.context.warning ?? 0 }),
  },
  gpus_busy: {
    kind: "share",
    better: "neutral",
    context: (i, t) => t("dashboard.kpi.gpus_busy.context", { machines: i.context.machines ?? 0 }),
  },
};

export default function KpisWidget({ layout, onLayoutChange, refreshToken }: WidgetProps) {
  const { t, i18n } = useTranslation();
  const [choosing, setChoosing] = useState(false);
  const ids = layout.kpis;
  const query = useKpis(ids, { refreshToken });
  const byId = new Map((query.data?.items ?? []).map((item) => [item.id, item]));

  return (
    <WidgetCard
      title={t("dashboard.widgets.kpis.title")}
      actions={
        <Tooltip title={t("dashboard.kpi.choose_title")}>
          <IconButton size="small" onClick={() => setChoosing(true)} aria-label={t("dashboard.kpi.choose_title")} sx={TOUCH_TARGET}>
            <TuneIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      }
      status={{
        loading: query.loading && ids.length > 0,
        error: query.error,
        hasData: Boolean(query.data),
        onRetry: query.refresh,
        stale: query.stale,
        updatedAt: query.updatedAt,
      }}
      skeleton={
        <StatGrid>
          {ids.map((id) => (
            <Skeleton key={id} variant="rounded" height={104} />
          ))}
        </StatGrid>
      }
      empty={ids.length === 0 ? <WidgetEmpty message={t("dashboard.kpi.none_chosen")} /> : undefined}
    >
      <StatGrid>
        {ids.map((id) => (
          <Tile key={id} id={id as KpiId} item={byId.get(id)} lang={i18n.language} />
        ))}
      </StatGrid>
      <KpiPicker
        open={choosing}
        chosen={ids}
        onClose={() => setChoosing(false)}
        onSave={(kpis) => {
          onLayoutChange({ kpis });
          setChoosing(false);
        }}
      />
    </WidgetCard>
  );
}

function Tile({ id, item, lang }: { id: KpiId; item?: KpiItem; lang: string }) {
  const { t } = useTranslation();
  const meta = META[id];
  const trend = item && meta ? trendOf(item, meta, lang) : null;
  return (
    <StatTile
      label={t(`dashboard.kpi.${id}.label`)}
      value={item && meta ? formatValue(item.value, meta.kind, lang, t) : "–"}
      trend={trend ? { ...trend, baseline: t(`dashboard.kpi.${id}.baseline`) } : null}
      detail={item && meta ? meta.context(item, t, lang) : t("dashboard.kpi.not_available")}
    />
  );
}

function formatValue(
  value: KpiItem["value"],
  kind: Kind,
  lang: string,
  t: (k: string) => string,
): string {
  if (value == null) return "–";
  if (kind === "money") {
    const lines = value as Money[];
    return lines.length ? lines.map((m) => fmtMoney(m.amount, m.currency, lang)).join(" · ") : t("dashboard.kpi.no_spend");
  }
  return kind === "share" ? fmtShare(value as number, lang) : fmtCompact(value as number, lang);
}

function trendOf(item: KpiItem, meta: KpiMeta, lang: string): Omit<TrendInfo, "baseline"> | null {
  let delta: number | null = null;
  let text = "";
  if (meta.kind === "money") {
    const now = item.value as Money[] | null;
    const before = item.previous as Money[] | null;
    // Only one currency on both sides can be compared.
    if (now?.length !== 1 || before?.length !== 1 || now[0].currency !== before[0].currency) return null;
    const [a, b] = [Number(now[0].amount), Number(before[0].amount)];
    if (!b) return null;
    delta = (a - b) / b;
    text = fmtChange(delta, lang);
  } else {
    const a = item.value as number | null;
    const b = item.previous as number | null;
    if (a == null || b == null) return null;
    if (meta.kind === "share") {
      delta = a - b;
      text = `${delta >= 0 ? "+" : "−"}${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(Math.abs(delta) * 100)} pp`;
    } else {
      if (!b) return null; // no baseline yet
      delta = (a - b) / b;
      text = fmtChange(delta, lang);
    }
  }
  const flat = Math.abs(delta) < 0.005;
  const direction = flat ? "flat" : delta > 0 ? "up" : "down";
  const tone =
    flat || meta.better === "neutral"
      ? "neutral"
      : (direction === "up") === (meta.better === "up")
        ? "good"
        : "bad";
  return { text, direction, tone };
}


function KpiPicker({
  open,
  chosen,
  onClose,
  onSave,
}: {
  open: boolean;
  chosen: string[];
  onClose: () => void;
  onSave: (kpis: string[]) => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string[]>(chosen);
  const offered = KPI_IDS.filter((id) => META[id]);

  function toggle(id: string) {
    setDraft((d) => (d.includes(id) ? d.filter((x) => x !== id) : [...d, id]));
  }

  return (
    <Dialog open={open} onClose={onClose} TransitionProps={{ onEnter: () => setDraft(chosen) }} fullWidth maxWidth="xs">
      <DialogTitle>{t("dashboard.kpi.choose_title")}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t("dashboard.kpi.choose_hint", { max: MAX_KPIS })}
        </Typography>
        <Stack>
          {offered.map((id) => (
            <FormControlLabel
              key={id}
              sx={{ minHeight: 44 }}
              control={
                <Checkbox
                  checked={draft.includes(id)}
                  disabled={!draft.includes(id) && draft.length >= MAX_KPIS}
                  onChange={() => toggle(id)}
                />
              }
              label={t(`dashboard.kpi.${id}.label`)}
            />
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} sx={{ minHeight: 44 }}>
          {t("common.cancel", { defaultValue: "Cancel" })}
        </Button>
        {/* Keep the order the user had, then add new ones at the end. */}
        <Button
          variant="contained"
          onClick={() => onSave([...chosen.filter((id) => draft.includes(id)), ...draft.filter((id) => !chosen.includes(id))])}
          sx={{ minHeight: 44 }}
        >
          {t("dashboard.done")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
