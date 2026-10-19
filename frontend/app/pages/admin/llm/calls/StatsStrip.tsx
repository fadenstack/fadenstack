import { useTranslation } from "react-i18next";

import type { LlmCallStats } from "~/api/llmCalls";
import { StatGrid, StatTile } from "~/components/dashboard/kit";

import { formatCost, formatCount, formatDuration, formatPercent } from "./format";

interface Props {
  stats: LlmCallStats | null;
  loading: boolean;
  errorsOnly: boolean;
  onToggleErrors: () => void;
}

/** What the filtered calls add up to: the numbers someone checks before opening one. */
export default function StatsStrip({ stats, loading, errorsOnly, onToggleErrors }: Props) {
  const { t } = useTranslation();
  const errorRate = stats?.error_rate ?? null;
  const has = (stats?.total ?? 0) > 0;
  return (
    <StatGrid min={130} gap={1}>
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_calls")}
        value={stats ? formatCount(stats.total) : null}
        loading={loading && !stats}
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_errors")}
        value={stats ? `${formatCount(stats.errors)} · ${formatPercent(errorRate ?? 0)}` : null}
        tone={errorRate !== null && errorRate >= 0.05 ? "error" : errorRate ? "warning" : "default"}
        loading={loading && !stats}
        onClick={onToggleErrors}
        selected={errorsOnly}
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_p50")}
        value={has ? formatDuration(stats?.p50_latency_ms) : null}
        loading={loading && !stats}
        emptyText="—"
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_p95")}
        value={has ? formatDuration(stats?.p95_latency_ms) : null}
        loading={loading && !stats}
        emptyText="—"
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_ttft")}
        value={stats?.p95_ttft_ms != null ? formatDuration(stats.p95_ttft_ms) : null}
        emptyHint={t("llm_calls.stat_ttft_empty")}
        loading={loading && !stats}
        emptyText="—"
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_tokens")}
        value={has ? `${formatCount(stats?.prompt_tokens)} → ${formatCount(stats?.completion_tokens)}` : null}
        loading={loading && !stats}
        emptyText="—"
      />
      <StatTile
        variant="compact"
        label={t("llm_calls.stat_cost")}
        value={stats?.estimated_total_cost != null ? formatCost(stats.estimated_total_cost) : null}
        emptyHint={t("llm_calls.stat_cost_empty")}
        loading={loading && !stats}
        emptyText="—"
      />
    </StatGrid>
  );
}
