/**
 * Chargeback: one month's usage and estimated cost per team (a team's figures include its sub-teams', the
 * rows indented by the tree) or per cost centre, for allocating costs internally. Every row covers at
 * least k people; smaller ones come merged into one row. The money is an internal estimate at this
 * instance's prices, and the page says so above the table. The same report downloads as CSV with FOCUS
 * column names.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DownloadIcon from "@mui/icons-material/Download";

import { budgetsApi, type ChargebackBy, type ChargebackRow } from "~/api/budgets";
import { BUDGETS_HELP } from "~/components/budgets/budgetsHelp";
import { budgetErrorText, fmtMonth, recentMonths } from "~/components/budgets/budgetFormat";
import { RangeToggle, WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { fmtInt } from "~/components/dashboard/format";
import { UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { byLabel } from "~/components/usage/UsageParts";
import { fmtCost } from "~/components/usage/usageFormat";
import { useAsyncData } from "~/lib/useAsyncData";

const BYS: ChargebackBy[] = ["team", "cost_center"];

/** A row's name: its label, or what a row without one stands for. */
export function chargebackLabel(row: ChargebackRow, by: ChargebackBy, k: number, t: (key: string, o?: Record<string, unknown>) => string): string {
  if (row.other) return t("budgets.chargeback.other_row", { k });
  if (row.key === null) return by === "team" ? t("budgets.chargeback.unassigned") : t("budgets.chargeback.no_cost_center");
  return row.label ?? row.key;
}

/** By team the server's order (the tree, top first); by cost centre by name; the merged rows last. */
export function chargebackOrder(rows: ChargebackRow[], by: ChargebackBy, lang?: string): ChargebackRow[] {
  const merged = rows.filter((r) => r.other);
  const rest = rows.filter((r) => !r.other);
  const named = by === "team" ? rest : byLabel(rest, (r) => r.label ?? r.key ?? "", lang);
  const unnamed = named.filter((r) => r.key === null);
  return [...named.filter((r) => r.key !== null), ...unnamed, ...merged];
}

export default function ChargebackTab() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const months = recentMonths();
  // The month that ended: the one a report is usually for.
  const [month, setMonth] = useState(months[1] ?? months[0]);
  const [by, setBy] = useState<ChargebackBy>("team");
  const report = useAsyncData(
    () =>
      budgetsApi.chargeback(month, by).catch((err: unknown) => {
        throw new Error(budgetErrorText(err, (key, options) => t(key, options)));
      }),
    [month, by],
  );
  const data = report.data;
  const k = data?.min_group_size ?? 5;
  const rows = data ? chargebackOrder(data.rows, data.by, lang) : [];
  const tt = (key: string, options?: Record<string, unknown>) => t(key, options);

  return (
    <Stack gap={2} data-budget-chargeback>
      <Typography variant="body2" color="text.secondary">
        {t("budgets.chargeback.intro")} <UsageHelpLink doc={BUDGETS_HELP.chargeback} />
      </Typography>

      <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} alignItems={{ xs: "stretch", sm: "center" }} flexWrap="wrap">
        <TextField
          select
          size="small"
          label={t("budgets.chargeback.month")}
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          sx={{ minWidth: 200 }}
          slotProps={{ htmlInput: { "data-budget-month": true } }}
        >
          {months.map((m) => (
            <MenuItem key={m} value={m}>
              {fmtMonth(m, lang)}
            </MenuItem>
          ))}
        </TextField>
        <RangeToggle
          value={by}
          options={BYS.map((b) => ({ value: b, label: t(`budgets.chargeback.by.${b}`) }))}
          onChange={setBy}
          ariaLabel={t("budgets.chargeback.by_label")}
        />
        <Box sx={{ flexGrow: 1 }} />
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          component="a"
          href={budgetsApi.chargebackCsvUrl(month, by)}
          download
        >
          {t("budgets.chargeback.download")}
        </Button>
      </Stack>

      <Alert severity="info" variant="outlined" data-budget-estimate>
        <AlertTitle>{t("budgets.chargeback.estimate_title")}</AlertTitle>
        {t("budgets.chargeback.estimate", { k })} {t("budgets.chargeback.download_hint")}
      </Alert>

      <WidgetCard
        title={fmtMonth(month, lang)}
        subtitle={t(`budgets.chargeback.by.${by}`)}
        status={{ loading: report.loading && !data, error: report.error, hasData: Boolean(data), onRetry: () => void report.refresh() }}
        empty={data && rows.length === 0 ? <WidgetEmpty message={t("budgets.chargeback.none")} /> : undefined}
      >
        {report.error && !data ? null : (
          <TableContainer>
            <Table size="small" aria-label={t("budgets.chargeback.title")}>
              <TableHead>
                <TableRow>
                  <TableCell>{t(`budgets.chargeback.by.${by}`)}</TableCell>
                  <TableCell align="right">{t("budgets.chargeback.col_people")}</TableCell>
                  <TableCell align="right">{t("usage.measures.input_tokens")}</TableCell>
                  <TableCell align="right">{t("usage.measures.output_tokens")}</TableCell>
                  <TableCell align="right">{t("budgets.chargeback.col_cost")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row, i) => {
                  const depth = by === "team" && row.path ? Math.max(row.path.length - 1, 0) : 0;
                  const cost = fmtCost(row.cost, lang);
                  return (
                    <TableRow key={`${row.key ?? "none"}-${row.other ? "other" : ""}-${i}`} data-chargeback-row={row.key ?? (row.other ? "other" : "none")}>
                      <TableCell sx={{ pl: 2 + depth * 2, maxWidth: 320, overflowWrap: "anywhere" }}>
                        {chargebackLabel(row, by, k, tt)}
                      </TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {row.people === null ? t("budgets.chargeback.people_k", { k }) : fmtInt(row.people, lang)}
                      </TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {fmtInt(row.input_tokens, lang)}
                      </TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {fmtInt(row.output_tokens, lang)}
                      </TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                        {cost ?? t("budgets.chargeback.no_cost")}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </WidgetCard>
    </Stack>
  );
}
