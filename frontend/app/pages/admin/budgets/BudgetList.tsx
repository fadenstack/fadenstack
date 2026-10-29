/**
 * The budgets, by name: for each whom it applies to, the period and its dates, the mode, how much is used
 * (a bar and the figures), the forecast, what a shadow budget would have refused, the alerts and their
 * owner, and the warnings: the teams below add up to more than this budget (allowed, each still holds), and
 * a money budget whose currency some models (or none) are priced in.
 *
 * A person's budget shows its limit only: what the person used is theirs alone (the server leaves it out),
 * and the page says so instead of showing a gap.
 */
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";

import type { AdminUser } from "~/api/admin";
import type { Budget } from "~/api/budgets";
import {
  barColor,
  barValue,
  fmtBudgetAmount,
  fmtDate,
  fmtPercent,
  fmtUsed,
} from "~/components/budgets/budgetFormat";
import { fmtInt } from "~/components/dashboard/format";
import { byLabel } from "~/components/usage/UsageParts";

/** Whom a budget applies to, in words: "Team · Sales", "The whole organisation", "Person". */
export function targetText(budget: Budget, t: (key: string) => string): string {
  if (budget.scope === "tenant") return t("budgets.list.whole_org");
  const label =
    budget.target_label ?? (budget.scope === "person" ? t("budgets.list.a_person") : t("budgets.list.removed_target"));
  return `${t(`budgets.scope.${budget.scope}`)} · ${label}`;
}

/**
 * The currencies a money budget does not count: models priced in another one. `none`: no model is priced in
 * the budget's currency at all. Unknown prices (an empty list): no warning.
 */
export function currencyWarning(budget: Budget, currencies: string[]): { kind: "none" | "others"; others: string[] } | null {
  if (budget.unit !== "money" || !budget.currency || currencies.length === 0) return null;
  const others = currencies.filter((c) => c !== budget.currency);
  if (!currencies.includes(budget.currency)) return { kind: "none", others };
  return others.length ? { kind: "others", others } : null;
}

export interface BudgetListProps {
  budgets: Budget[];
  users: AdminUser[];
  /** The currencies models are priced in (empty: not known). */
  currencies: string[];
  onEdit: (budget: Budget) => void;
  onDelete: (budget: Budget) => void;
}

export default function BudgetList({ budgets, users, currencies, onEdit, onDelete }: BudgetListProps) {
  const { i18n } = useTranslation();
  return (
    <Stack gap={1.5} component="ul" sx={{ listStyle: "none", m: 0, p: 0 }} data-budget-list>
      {byLabel(budgets, (b) => b.name, i18n.language).map((budget) => (
        <BudgetRow
          key={budget.id}
          budget={budget}
          owner={users.find((u) => u.id === budget.owner_id)?.email ?? null}
          currencies={currencies}
          onEdit={() => onEdit(budget)}
          onDelete={() => onDelete(budget)}
        />
      ))}
    </Stack>
  );
}

function BudgetRow({
  budget,
  owner,
  currencies,
  onEdit,
  onDelete,
}: {
  budget: Budget;
  owner: string | null;
  currencies: string[];
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const tt = (key: string, options?: Record<string, unknown>) => t(key, options);
  const shown = !budget.suppressed && budget.spent !== null;
  const currency = currencyWarning(budget, currencies);
  const forecastBefore = budget.forecast_end && budget.forecast_end <= budget.period_end ? budget.forecast_end : null;

  return (
    <Paper component="li" variant="outlined" sx={{ p: 2 }} data-budget-row={budget.id} aria-label={budget.name}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={1}>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 700, overflowWrap: "anywhere" }}>
            {budget.name}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere" }} data-budget-target>
            {targetText(budget, (key) => t(key))}
          </Typography>
        </Box>
        <Stack direction="row" gap={0.5} sx={{ flexShrink: 0 }}>
          <Tooltip title={t("common.edit")}>
            <IconButton size="small" onClick={onEdit} aria-label={t("budgets.list.edit", { name: budget.name })}>
              <EditOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.delete")}>
            <IconButton size="small" onClick={onDelete} aria-label={t("budgets.list.delete", { name: budget.name })}>
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      <Stack direction="row" gap={0.75} flexWrap="wrap" sx={{ my: 1 }}>
        <Chip size="small" variant="outlined" label={t(`budgets.period.${budget.period}`)} />
        <Chip
          size="small"
          variant="outlined"
          color={budget.mode === "hard" ? "primary" : "default"}
          label={t(`budgets.mode.${budget.mode}`)}
          data-budget-mode={budget.mode}
        />
        <Chip
          size="small"
          variant="outlined"
          label={t("budgets.list.limit", { amount: fmtBudgetAmount(budget.amount, budget.unit, budget.currency, tt, lang) })}
        />
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {t("budgets.list.period_range", { start: fmtDate(budget.period_start, lang), end: fmtDate(budget.period_end, lang) })}
      </Typography>

      {shown ? (
        <Box sx={{ mt: 1 }} data-budget-spent>
          <Stack direction="row" justifyContent="space-between" gap={1} flexWrap="wrap">
            <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
              {fmtUsed(budget.spent!, budget.amount, budget.unit, budget.currency, tt, lang)}
            </Typography>
            {budget.percent !== null && (
              <Typography variant="body2" sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                {fmtPercent(budget.percent, lang)}
              </Typography>
            )}
          </Stack>
          <LinearProgress
            variant="determinate"
            value={barValue(budget.percent)}
            color={barColor(budget.percent)}
            aria-label={t("budgets.list.used_label", { name: budget.name })}
            sx={{ mt: 0.75, height: 8, borderRadius: 999 }}
          />
        </Box>
      ) : (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }} data-budget-suppressed>
          {budget.scope === "person" ? t("budgets.list.suppressed") : t("budgets.list.spent_unknown")}
        </Typography>
      )}

      <Stack gap={0.5} sx={{ mt: 1 }}>
        {shown && forecastBefore && (
          <Typography variant="body2" color="warning.main" data-budget-forecast>
            {t("budgets.list.forecast", { date: fmtDate(forecastBefore, lang) })}
          </Typography>
        )}
        {budget.mode === "shadow" && (
          <Typography variant="body2" data-budget-would-refuse>
            {budget.would_refuse > 0
              ? t("budgets.list.would_refuse", { count: budget.would_refuse, n: fmtInt(budget.would_refuse, lang) })
              : t("budgets.list.would_refuse_none")}
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary">
          {budget.thresholds.length
            ? t("budgets.list.thresholds", { list: budget.thresholds.map((n) => `${n} %`).join(", ") })
            : t("budgets.list.no_thresholds")}
          {owner ? ` · ${t("budgets.list.owner", { owner })}` : ""}
        </Typography>
      </Stack>

      {(budget.children_exceed || currency) && (
        <Stack gap={1} sx={{ mt: 1.5 }}>
          {budget.children_exceed && (
            <Alert severity="warning" variant="outlined" data-budget-warning="children_exceed">
              {t("budgets.list.children_exceed")}
            </Alert>
          )}
          {currency && (
            <Alert severity="warning" variant="outlined" data-budget-warning="currency">
              {currency.kind === "none"
                ? t("budgets.list.currency_none", { currency: budget.currency })
                : t("budgets.list.currency_others", { currency: budget.currency, others: currency.others.join(", ") })}
            </Alert>
          )}
        </Stack>
      )}
    </Paper>
  );
}
