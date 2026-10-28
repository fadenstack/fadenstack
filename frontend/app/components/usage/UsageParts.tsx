/**
 * The parts both usage pages draw (My usage and the administrators' Usage):
 * the totals as tiles, tokens over time, and a table of measures per row.
 *
 * None of them orders anything by usage: a table keeps the order it is given
 * (the pages give it by name), and there are no sort controls on the figures
 * -- the pages must not rank people or read as a league table (M11).
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

import Box from "@mui/material/Box";

import { StatGrid, StatTile, TimeSeriesChart, WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { fmtInt } from "~/components/dashboard/format";
import { isHidden, type AdminBucket, type Measures, type UsagePeriod, type UsageSection } from "~/api/usage";

import { fmtCost, periodLabel } from "./usageFormat";

/**
 * Requests, tokens in and out, the estimated cost, failures: the totals of a period. Totals the server
 * hides (fewer than k people in the range) show as gaps, with `hiddenHint` saying why.
 */
export function UsageTotals({ totals, before, hiddenHint }: { totals: Measures; before?: ReactNode; hiddenHint?: string }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  if (isHidden(totals)) {
    const gap = (label: string) => <StatTile label={label} value={null} emptyHint={hiddenHint} />;
    return (
      <StatGrid>
        {before}
        {gap(t("usage.measures.requests"))}
        {gap(t("usage.measures.input_tokens"))}
        {gap(t("usage.measures.output_tokens"))}
        {gap(t("usage.measures.cost"))}
      </StatGrid>
    );
  }
  const cost = fmtCost(totals.cost, lang);
  return (
    <StatGrid>
      {before}
      <StatTile
        label={t("usage.measures.requests")}
        value={fmtInt(totals.requests, lang)}
        detail={t("usage.failed_detail", { failed: fmtInt(totals.failed, lang), refused: fmtInt(totals.refused, lang) })}
      />
      <StatTile
        label={t("usage.measures.input_tokens")}
        value={fmtInt(totals.input_tokens, lang)}
        detail={t("usage.input_detail", { cached: fmtInt(totals.cached_input_tokens, lang) })}
      />
      <StatTile
        label={t("usage.measures.output_tokens")}
        value={fmtInt(totals.output_tokens, lang)}
        detail={t("usage.output_detail", { reasoning: fmtInt(totals.reasoning_tokens, lang) })}
      />
      <StatTile
        label={t("usage.measures.cost")}
        value={cost}
        emptyHint={t("usage.cost_none")}
        detail={cost ? t("usage.cost_hint") : undefined}
      />
    </StatGrid>
  );
}

/**
 * Input and output tokens per period. A period without measures (an hour
 * with fewer than k people) is a gap in the line, not a zero.
 */
export function UsageSeriesChart({
  series,
  bucket,
}: {
  series: (UsagePeriod & { suppressed?: boolean })[];
  bucket: AdminBucket;
}) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const value = (p: UsagePeriod & { suppressed?: boolean }, n: number | null) => (p.suppressed ? null : n);
  return (
    <TimeSeriesChart
      x={series.map((p) => p.period)}
      xFormat={(v) => periodLabel(String(v), bucket, i18n.language)}
      series={[
        {
          id: "input",
          label: t("usage.series_input"),
          data: series.map((p) => value(p, p.input_tokens)),
          color: theme.palette.primary.main,
        },
        {
          id: "output",
          label: t("usage.series_output"),
          data: series.map((p) => value(p, p.output_tokens)),
          color: theme.palette.dashboard.residency.machines,
        },
      ]}
      stacked
      ariaLabel={t("usage.series_title")}
      height={220}
    />
  );
}

export interface MeasuresRow {
  key: string;
  label: ReactNode;
  /** Cells before the measures (people, a period). */
  lead?: ReactNode[];
  /** null: not shown (fewer than k people); `hidden` says why. */
  measures: Measures | null;
  hidden?: string;
}

/**
 * One row per group, period or person with its measures, in the order given.
 * The headers are plain text: no column sorts by usage.
 */
export function MeasuresTable({
  labelHeader,
  leadHeaders = [],
  rows,
  ariaLabel,
}: {
  labelHeader: string;
  leadHeaders?: string[];
  rows: MeasuresRow[];
  ariaLabel: string;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const empty = (hint?: string) => (
    <Tooltip title={hint ?? ""}>
      <Typography component="span" variant="body2" color="text.disabled">
        –
      </Typography>
    </Tooltip>
  );
  return (
    <TableContainer>
      <Table size="small" aria-label={ariaLabel}>
        <TableHead>
          <TableRow>
            <TableCell>{labelHeader}</TableCell>
            {leadHeaders.map((h) => (
              <TableCell key={h} align="right">
                {h}
              </TableCell>
            ))}
            <TableCell align="right">{t("usage.measures.requests")}</TableCell>
            <TableCell align="right">{t("usage.measures.input_tokens")}</TableCell>
            <TableCell align="right">{t("usage.measures.output_tokens")}</TableCell>
            <TableCell align="right">{t("usage.measures.cost")}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            // Measures the server hid (fewer than k people) are no figures either.
            const m = row.measures && !isHidden(row.measures) ? row.measures : null;
            const cost = m ? fmtCost(m.cost, lang) : null;
            return (
              <TableRow key={row.key}>
                <TableCell sx={{ maxWidth: 280, overflowWrap: "anywhere" }}>{row.label}</TableCell>
                {(row.lead ?? []).map((cell, i) => (
                  <TableCell key={i} align="right">
                    {cell}
                  </TableCell>
                ))}
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {m ? fmtInt(m.requests, lang) : empty(row.hidden)}
                </TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {m ? fmtInt(m.input_tokens, lang) : empty(row.hidden)}
                </TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {m ? fmtInt(m.output_tokens, lang) : empty(row.hidden)}
                </TableCell>
                <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                  {m ? (cost ?? empty(t("usage.cost_none"))) : empty(row.hidden)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

/** A block from an extension (budgets, later): its heading by kind, then its fields as they come. */
export function UsageSectionCard({ section }: { section: UsageSection }) {
  const { t, i18n } = useTranslation();
  const number = new Intl.NumberFormat(i18n.language);
  const show = (value: unknown): string => {
    if (value === null || value === undefined) return "–";
    if (typeof value === "number") return number.format(value);
    if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
    if (typeof value === "string") return value;
    return JSON.stringify(value);
  };
  const fields = Object.entries(section.data ?? {});
  return (
    <WidgetCard title={t(`usage.sections.${section.kind}`, { defaultValue: section.kind })}>
      {fields.length === 0 ? (
        <WidgetEmpty message={t("usage.no_data")} />
      ) : (
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 0.75, m: 0 }}>
          {fields.map(([key, value]) => (
            <Box key={key} sx={{ display: "contents" }}>
              <Typography component="dt" variant="body2" color="text.secondary">
                {t(`usage.section_fields.${key}`, { defaultValue: key })}
              </Typography>
              <Typography component="dd" variant="body2" sx={{ m: 0, textAlign: "right", overflowWrap: "anywhere" }}>
                {show(value)}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </WidgetCard>
  );
}

/** Rows by their label, as people read a list (the API's own order is kept for equal labels). */
export function byLabel<T>(items: T[], label: (item: T) => string, lang?: string): T[] {
  const collator = new Intl.Collator(lang, { sensitivity: "base", numeric: true });
  return [...items].sort((a, b) => collator.compare(label(a), label(b)));
}
