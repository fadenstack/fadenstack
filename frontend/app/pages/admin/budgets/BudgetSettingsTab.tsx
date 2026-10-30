/**
 * The Budgets page's settings:
 * - Budgets for single people, off by default. The switch says what turning it on means (only the person
 *   sees their use and gets its alerts; administrators see the limit; agree it with the works council). It
 *   is a protected setting: changing it needs root mode, and the switch stays locked without it.
 * - Usage groups from cost centres: when provisioning sends a person's cost centre, a rule makes a team
 *   their usage group. The rules are a table of cost centre and team, each cost centre once.
 */
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";

import type { Group } from "~/api/admin";
import { BudgetProblemsError, budgetsApi, type BudgetSettings, type CostCenterRule } from "~/api/budgets";
import { COST_CENTER_MAX } from "~/api/teamAdmin";
import { BUDGETS_HELP } from "~/components/budgets/budgetsHelp";
import { budgetErrorText } from "~/components/budgets/budgetFormat";
import { UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { pickableTeams } from "~/lib/teamTree";

interface Draft {
  person_budgets: boolean;
  cost_center_rule: boolean;
  rules: CostCenterRule[];
}

function toDraft(s: BudgetSettings): Draft {
  return {
    person_budgets: s.person_budgets,
    cost_center_rule: s.cost_center_rule,
    rules: (s.cost_center_rules ?? []).map((r) => ({ ...r })),
  };
}

const sameRules = (a: CostCenterRule[], b: CostCenterRule[]) =>
  a.length === b.length && a.every((r, i) => r.cost_center === b[i].cost_center && r.group_id === b[i].group_id);

/** What is wrong with the rules as typed: a row without a cost centre or a team, or a cost centre twice. */
export function rulesProblem(rules: CostCenterRule[]): "incomplete" | "duplicate" | null {
  if (rules.some((r) => !r.cost_center.trim() || !r.group_id)) return "incomplete";
  const seen = new Set<string>();
  for (const rule of rules) {
    const key = rule.cost_center.trim().toLowerCase();
    if (seen.has(key)) return "duplicate";
    seen.add(key);
  }
  return null;
}

function SectionTitle({ title, doc }: { title: string; doc: string }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="baseline" gap={1}>
      <Typography variant="subtitle1" component="h2">
        {title}
      </Typography>
      <UsageHelpLink doc={doc} />
    </Stack>
  );
}

export interface BudgetSettingsTabProps {
  teams: Group[];
  /** After a save, with what the server now holds. */
  onSaved?: (settings: BudgetSettings) => void;
}

export default function BudgetSettingsTab({ teams, onSaved }: BudgetSettingsTabProps) {
  const { t, i18n } = useTranslation();
  const rootModeActive = useOutletContext<{ rootModeActive?: boolean } | undefined>()?.rootModeActive ?? false;
  const [saved, setSaved] = useState<BudgetSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    budgetsApi
      .settings()
      .then((s) => {
        if (!live) return;
        setSaved(s);
        setDraft(toDraft(s));
      })
      .catch((err: unknown) => {
        if (live) setLoadError(budgetErrorText(err, (key, options) => t(key, options)));
      });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!draft || !saved) {
    return loadError ? (
      <Alert severity="error">{loadError}</Alert>
    ) : (
      <CircularProgress size={20} aria-label={t("common.loading")} />
    );
  }

  const problem = rulesProblem(draft.rules);
  const changes: Partial<BudgetSettings> = {};
  if (draft.person_budgets !== saved.person_budgets) changes.person_budgets = draft.person_budgets;
  if (draft.cost_center_rule !== saved.cost_center_rule) changes.cost_center_rule = draft.cost_center_rule;
  const cleanRules = draft.rules.map((r) => ({ cost_center: r.cost_center.trim(), group_id: r.group_id }));
  if (!sameRules(cleanRules, saved.cost_center_rules ?? [])) changes.cost_center_rules = cleanRules;
  const dirty = Object.keys(changes).length > 0;
  const teamRows = pickableTeams(teams, i18n.language);

  function edit(change: Partial<Draft>) {
    setDraft((d) => (d ? { ...d, ...change } : d));
    setStatus(null);
    setError(null);
  }

  function setRule(index: number, change: Partial<CostCenterRule>) {
    if (!draft) return;
    edit({ rules: draft.rules.map((r, i) => (i === index ? { ...r, ...change } : r)) });
  }

  async function save() {
    if (!dirty || problem) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const next = await budgetsApi.updateSettings(changes);
      setSaved(next);
      setDraft(toDraft(next));
      setStatus(t("budgets.settings.saved"));
      onSaved?.(next);
    } catch (err) {
      if (err instanceof BudgetProblemsError) {
        setError(err.problems.map((p) => t(`budgets.problems.${p.code}`, { defaultValue: t("budgets.problems.invalid") })).join(" "));
      } else {
        setError(budgetErrorText(err, (key, options) => t(key, options)));
      }
    } finally {
      setBusy(false);
    }
  }

  // Switching person budgets is protected; turning it off again needs root mode too.
  const personLocked = !rootModeActive || busy;

  return (
    <Stack spacing={2} sx={{ maxWidth: 880 }} data-budget-settings>
      <Paper sx={{ p: 2 }}>
        <SectionTitle title={t("budgets.settings.person_title")} doc={BUDGETS_HELP.privacy} />
        <Typography variant="body2" color="text.secondary">
          {t("budgets.settings.person_intro")}
        </Typography>
        <Divider sx={{ my: 1.5 }} />
        <FormControlLabel
          control={
            <Switch
              checked={draft.person_budgets}
              disabled={personLocked}
              onChange={(e) => edit({ person_budgets: e.target.checked })}
              slotProps={{ input: { role: "switch", "aria-label": t("budgets.settings.person_budgets") } }}
            />
          }
          label={
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {t("budgets.settings.person_budgets")}
            </Typography>
          }
        />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }} data-budget-person-text>
          {draft.person_budgets ? t("budgets.settings.person_on_text") : t("budgets.settings.person_off_text")}
        </Typography>
        <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
          {t("budgets.settings.works_council")}
        </Alert>
        <Typography
          variant="caption"
          color={rootModeActive ? "text.secondary" : "warning.main"}
          sx={{ display: "block", mt: 0.75 }}
          data-budget-root-hint
        >
          {rootModeActive ? t("budgets.settings.root_mode_hint") : t("budgets.settings.root_mode_needed")}
        </Typography>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <SectionTitle title={t("budgets.settings.cost_center_title")} doc={BUDGETS_HELP.settings} />
        <Typography variant="body2" color="text.secondary">
          {t("budgets.settings.cost_center_intro")}
        </Typography>
        <Divider sx={{ my: 1.5 }} />
        <FormControlLabel
          control={
            <Switch
              checked={draft.cost_center_rule}
              disabled={busy}
              onChange={(e) => edit({ cost_center_rule: e.target.checked })}
              slotProps={{ input: { role: "switch", "aria-label": t("budgets.settings.cost_center_rule") } }}
            />
          }
          label={
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {t("budgets.settings.cost_center_rule")}
            </Typography>
          }
        />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, mb: 1.5 }}>
          {draft.cost_center_rule ? t("budgets.settings.cost_center_on_text") : t("budgets.settings.cost_center_off_text")}
        </Typography>

        <Typography variant="subtitle2" component="h3" sx={{ mb: 1 }}>
          {t("budgets.settings.rules_title")}
        </Typography>
        <Stack gap={1.5} data-budget-rules>
          {draft.rules.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              {t("budgets.settings.rules_none")}
            </Typography>
          )}
          {draft.rules.map((rule, index) => (
            <Stack
              key={index}
              direction={{ xs: "column", sm: "row" }}
              gap={1}
              alignItems={{ xs: "stretch", sm: "center" }}
              data-budget-rule={index}
            >
              <TextField
                size="small"
                label={t("budgets.settings.rule_cost_center")}
                value={rule.cost_center}
                onChange={(e) => setRule(index, { cost_center: e.target.value })}
                slotProps={{ htmlInput: { maxLength: COST_CENTER_MAX, spellCheck: false } }}
                sx={{ flex: 1 }}
              />
              <TextField
                select
                size="small"
                label={t("budgets.settings.rule_team")}
                value={rule.group_id}
                onChange={(e) => setRule(index, { group_id: e.target.value })}
                sx={{ flex: 1 }}
              >
                {teamRows.map(({ team, depth }) => (
                  <MenuItem key={team.id} value={team.id} sx={{ pl: 2 + (depth - 1) * 2 }}>
                    {team.name}
                  </MenuItem>
                ))}
                {rule.group_id && !teamRows.some((r) => r.team.id === rule.group_id) && (
                  <MenuItem value={rule.group_id}>{t("budgets.list.removed_target")}</MenuItem>
                )}
              </TextField>
              <Box sx={{ alignSelf: { xs: "flex-end", sm: "center" } }}>
                <IconButton
                  size="small"
                  onClick={() => edit({ rules: draft.rules.filter((_, i) => i !== index) })}
                  aria-label={t("budgets.settings.rule_remove", { n: index + 1 })}
                >
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </Box>
            </Stack>
          ))}
          <Box>
            <Button
              size="small"
              startIcon={<AddIcon />}
              onClick={() => edit({ rules: [...draft.rules, { cost_center: "", group_id: "" }] })}
            >
              {t("budgets.settings.rule_add")}
            </Button>
          </Box>
          {problem && (
            <Typography variant="caption" color="error" data-budget-rules-problem>
              {t(`budgets.settings.rules_${problem}`)}
            </Typography>
          )}
        </Stack>
      </Paper>

      {error && <Alert severity="error">{error}</Alert>}
      {status && <Alert severity="success">{status}</Alert>}
      <Box>
        <Button variant="contained" disabled={!dirty || Boolean(problem) || busy} onClick={() => void save()}>
          {busy ? t("common.saving") : t("common.save")}
        </Button>
      </Box>
    </Stack>
  );
}
