/**
 * The budgets that apply to the person, on My usage (the `budgets` block): for each whose it is (their own,
 * their team's, an agent's, the organisation's), how much is used of it as a bar where the person may see
 * it, when the period ends and what happens when it is used up; then the alerts of this period.
 *
 * What a person used of their own budget is theirs alone (nobody else is shown it); a team's or the
 * organisation's use shows only where enough people share the budget, so it never points to one of them.
 */
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { readMyBudgets } from "~/api/budgets";
import type { UsageSection } from "~/api/usage";
import { WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { UsageHelpLink } from "~/components/usage/UsageHelpLink";

import { BUDGETS_HELP } from "./budgetsHelp";
import { barColor, barValue, fmtBudgetAmount, fmtDate, fmtPercent, fmtUsed } from "./budgetFormat";

export default function MyBudgetsCard({ section }: { section: UsageSection }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const tt = (key: string, options?: Record<string, unknown>) => t(key, options);
  const { budgets, alerts } = readMyBudgets(section.data);
  const personal = budgets.some((b) => b.kind === "personal");

  return (
    <WidgetCard
      title={t("budgets.my.title")}
      subtitle={t("budgets.my.subtitle")}
      actions={<UsageHelpLink doc={BUDGETS_HELP.own} />}
      empty={budgets.length === 0 ? <WidgetEmpty message={t("budgets.my.none")} /> : undefined}
    >
      <Stack gap={1.75} data-my-budgets>
        {budgets.map((budget, i) => (
          <Box key={`${budget.kind}-${budget.name}-${i}`} data-my-budget={budget.kind}>
            <Stack direction="row" justifyContent="space-between" gap={1} flexWrap="wrap">
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: "anywhere" }}>
                  {budget.name}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {t(`budgets.my.kind.${budget.kind}`)}
                  {budget.spent === null
                    ? ` · ${fmtBudgetAmount(budget.amount, budget.unit, budget.currency, tt, lang)}`
                    : ""}
                </Typography>
              </Box>
              {budget.spent !== null && (
                <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {fmtUsed(budget.spent, budget.amount, budget.unit, budget.currency, tt, lang)}
                  {budget.percent !== null ? ` · ${fmtPercent(budget.percent, lang)}` : ""}
                </Typography>
              )}
            </Stack>
            {budget.spent !== null ? (
              <LinearProgress
                variant="determinate"
                value={barValue(budget.percent)}
                color={barColor(budget.percent)}
                aria-label={t("budgets.list.used_label", { name: budget.name })}
                sx={{ my: 0.75, height: 8, borderRadius: 999 }}
              />
            ) : (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }} data-my-budget-hidden>
                {t("budgets.my.spent_hidden")}
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {budget.period_end ? `${t("budgets.my.until", { date: fmtDate(budget.period_end, lang) })} ` : ""}
              {t(`budgets.my.mode.${budget.mode}`)}
            </Typography>
          </Box>
        ))}

        {alerts.length > 0 && (
          <Box data-my-budget-alerts>
            <Typography variant="subtitle2" component="h3">
              {t("budgets.my.alerts_title")}
            </Typography>
            <Stack component="ul" sx={{ m: 0, pl: 2.5 }}>
              {alerts.map((alert, i) => (
                <Typography component="li" variant="body2" key={`${alert.name}-${alert.kind}-${alert.threshold}-${i}`}>
                  {alert.kind === "forecast" || alert.threshold === null
                    ? t("budgets.my.alert_forecast", { name: alert.name, date: fmtDate(alert.at, lang) })
                    : t("budgets.my.alert", { name: alert.name, threshold: alert.threshold, date: fmtDate(alert.at, lang) })}
                </Typography>
              ))}
            </Stack>
          </Box>
        )}

        <Typography variant="caption" color="text.secondary">
          {personal ? `${t("budgets.my.private")} ` : ""}
          {t("budgets.my.ask")}
        </Typography>
      </Stack>
    </WidgetCard>
  );
}
