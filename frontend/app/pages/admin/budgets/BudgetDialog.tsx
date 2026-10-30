/**
 * A new budget, or a change to one: whom it applies to (the organisation, a team picked from the tree, an
 * agent, or, while budgets for single people are on, one person), tokens or money in a currency, the
 * amount, the period, the mode, the alert thresholds and an owner who gets the alerts too.
 *
 * The form checks what it can before it sends (a name, a target, an amount above 0, three-letter currency,
 * thresholds as whole percentages); the server checks the rest and names the fields, and each code is
 * worded under its field: a team too small for a budget of its own (fewer than k people unless
 * person-level reporting is on), a budget larger than its parent team's, person budgets off (a personal
 * agent counts as its person), a budget that exists already. Whom a budget applies to is fixed once it
 * exists: an edit changes everything else.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormHelperText from "@mui/material/FormHelperText";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import type { AdminUser, Group } from "~/api/admin";
import type { Agent } from "~/api/agents";
import {
  BUDGET_MODES,
  BUDGET_PERIODS,
  BudgetProblemsError,
  DEFAULT_THRESHOLDS,
  NAME_MAX,
  budgetsApi,
  currencyOk,
  parseAmount,
  parseThresholds,
  type Budget,
  type BudgetMode,
  type BudgetPeriod,
  type BudgetProblem,
  type BudgetScope,
  type BudgetUnit,
  type BudgetWrite,
} from "~/api/budgets";
import { BUDGETS_HELP } from "~/components/budgets/budgetsHelp";
import { budgetErrorText } from "~/components/budgets/budgetFormat";
import { UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { pickableTeams } from "~/lib/teamTree";

/** The form as typed: amounts and thresholds as text until they are sent. */
export interface BudgetForm {
  name: string;
  scope: BudgetScope;
  target_id: string;
  unit: BudgetUnit;
  currency: string;
  amount: string;
  period: BudgetPeriod;
  mode: BudgetMode;
  thresholds: string;
  owner_id: string;
}

export function formFromBudget(budget: Budget | null, currency = ""): BudgetForm {
  if (!budget) {
    return {
      name: "",
      scope: "team",
      target_id: "",
      unit: "tokens",
      currency,
      amount: "",
      period: "month",
      mode: "hard",
      thresholds: DEFAULT_THRESHOLDS.join(", "),
      owner_id: "",
    };
  }
  return {
    name: budget.name,
    scope: budget.scope,
    target_id: budget.target_id ?? "",
    unit: budget.unit,
    currency: budget.currency ?? currency,
    amount: String(budget.amount),
    period: budget.period,
    mode: budget.mode,
    thresholds: budget.thresholds.join(", "),
    owner_id: budget.owner_id ?? "",
  };
}

/** What the form would send, and what is wrong with it before it is sent. */
export function checkForm(form: BudgetForm): { body: BudgetWrite | null; problems: BudgetProblem[] } {
  const problems: BudgetProblem[] = [];
  const name = form.name.trim();
  if (!name) problems.push({ field: "name", code: "required" });
  else if (name.length > NAME_MAX) problems.push({ field: "name", code: "invalid" });
  if (form.scope !== "tenant" && !form.target_id) problems.push({ field: "target_id", code: "required" });
  const currency = form.currency.trim().toUpperCase();
  if (form.unit === "money") {
    if (!currency) problems.push({ field: "currency", code: "required" });
    else if (!currencyOk(currency)) problems.push({ field: "currency", code: "invalid" });
  }
  const amount = parseAmount(form.amount, form.unit);
  if (!form.amount.trim()) problems.push({ field: "amount", code: "required" });
  else if (amount === null) problems.push({ field: "amount", code: "invalid" });
  const thresholds = parseThresholds(form.thresholds);
  if (thresholds === null) problems.push({ field: "thresholds", code: "invalid" });
  if (problems.length || amount === null || thresholds === null) return { body: null, problems };
  return {
    body: {
      name,
      scope: form.scope,
      target_id: form.scope === "tenant" ? null : form.target_id,
      unit: form.unit,
      currency: form.unit === "money" ? currency : null,
      amount,
      period: form.period,
      mode: form.mode,
      thresholds,
      owner_id: form.owner_id || null,
    },
    problems,
  };
}

/** The fields of *body* that differ from *budget* (an edit sends only those). */
export function changedFields(budget: Budget, body: BudgetWrite): Partial<BudgetWrite> {
  const out: Partial<BudgetWrite> = {};
  if (body.name !== budget.name) out.name = body.name;
  if (body.unit !== budget.unit) out.unit = body.unit;
  if (body.currency !== budget.currency) out.currency = body.currency;
  if (body.amount !== budget.amount) out.amount = body.amount;
  if (body.period !== budget.period) out.period = body.period;
  if (body.mode !== budget.mode) out.mode = body.mode;
  if (body.thresholds.join(",") !== budget.thresholds.join(",")) out.thresholds = body.thresholds;
  if (body.owner_id !== budget.owner_id) out.owner_id = body.owner_id;
  return out;
}

const FORM_FIELDS = ["name", "scope", "target_id", "unit", "currency", "amount", "period", "mode", "thresholds", "owner_id"];

export interface BudgetDialogProps {
  open: boolean;
  /** null: a new budget. */
  budget: Budget | null;
  /** Budgets for single people are switched on. */
  personBudgets: boolean;
  /** k, and whether person-level reporting is on: the rule for small teams. */
  minGroupSize: number;
  personReporting: boolean;
  teams: Group[];
  agents: Agent[];
  users: AdminUser[];
  /** The currencies models are priced in; the first is a new money budget's. */
  currencies: string[];
  onClose: () => void;
  onSaved: (budget: Budget) => void;
}

export default function BudgetDialog(props: BudgetDialogProps) {
  const { open, budget, personBudgets, minGroupSize, personReporting, teams, agents, users, currencies, onClose, onSaved } =
    props;
  const { t, i18n } = useTranslation();
  const [form, setForm] = useState<BudgetForm>(() => formFromBudget(budget, currencies[0]));
  const [problems, setProblems] = useState<BudgetProblem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const editing = budget !== null;

  // A fresh form each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setForm(formFromBudget(budget, currencies[0]));
    setProblems([]);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, budget]);

  const scopes = useMemo<BudgetScope[]>(() => {
    const list: BudgetScope[] = ["tenant", "team", "agent"];
    // A person's budget stays visible on its own edit even while person budgets are switched off.
    if (personBudgets || budget?.scope === "person") list.push("person");
    return list;
  }, [personBudgets, budget?.scope]);

  function set(change: Partial<BudgetForm>) {
    setForm((prev) => ({ ...prev, ...change }));
    const fields = Object.keys(change);
    if (change.scope) fields.push("target_id");
    if (change.unit) fields.push("amount", "currency");
    setProblems((prev) => prev.filter((p) => !fields.includes(p.field)));
    setError(null);
  }

  /** The problem with *field* in words: the field's own wording when it has one. */
  function problemText(field: string): string | undefined {
    const found = problems.find((p) => p.field === field);
    if (!found) return undefined;
    return codeText(found);
  }

  function codeText(problem: BudgetProblem): string {
    const params = { k: minGroupSize };
    for (const key of [
      `budgets.problems.${problem.field}_${form.scope}_${problem.code}`,
      `budgets.problems.${problem.field}_${problem.code}`,
      `budgets.problems.${problem.code}`,
    ]) {
      if (i18n.exists(key)) return t(key, params);
    }
    return t("budgets.problems.invalid");
  }

  function check(field: string, help?: string) {
    const problem = problemText(field);
    return { error: Boolean(problem), helperText: problem ?? help };
  }

  async function save() {
    const { body, problems: local } = checkForm(form);
    if (!body) {
      setProblems(local);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = budget ? await budgetsApi.update(budget.id, changedFields(budget, body)) : await budgetsApi.create(body);
      onSaved(saved);
    } catch (err) {
      if (err instanceof BudgetProblemsError) setProblems(err.problems);
      else setError(budgetErrorText(err, (key, options) => t(key, options)));
    } finally {
      setBusy(false);
    }
  }

  // Problems the server named for no field of this form: listed above the buttons.
  const loose = problems.filter((p) => !FORM_FIELDS.includes(p.field));
  const teamRows = pickableTeams(teams, i18n.language);
  const owner = users.find((u) => u.id === form.owner_id) ?? null;
  const person = users.find((u) => u.id === form.target_id) ?? null;

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm" aria-labelledby="budget-dialog-title">
      <DialogTitle id="budget-dialog-title">
        {editing ? t("budgets.dialog.edit_title") : t("budgets.dialog.create_title")}
      </DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }} data-budget-form>
          <Alert severity="info" variant="outlined" data-budget-tree-rule>
            {t("budgets.dialog.tree_rule")} <UsageHelpLink doc={BUDGETS_HELP.tree} />
          </Alert>

          <TextField
            size="small"
            label={t("budgets.dialog.name")}
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            {...check("name", t("budgets.dialog.name_help"))}
            slotProps={{ htmlInput: { maxLength: NAME_MAX, "data-budget-field": "name" } }}
            fullWidth
          />

          <TextField
            select
            size="small"
            label={t("budgets.dialog.scope")}
            value={form.scope}
            disabled={editing}
            onChange={(e) => set({ scope: e.target.value as BudgetScope, target_id: "" })}
            {...check("scope", t(`budgets.dialog.scope_help.${form.scope}`))}
            slotProps={{ htmlInput: { "data-budget-field": "scope" } }}
            fullWidth
          >
            {scopes.map((scope) => (
              <MenuItem key={scope} value={scope}>
                {t(`budgets.scope.${scope}`)}
              </MenuItem>
            ))}
          </TextField>
          {!personBudgets && !editing && (
            <Typography variant="caption" color="text.secondary" sx={{ mt: -1.5 }} data-budget-person-off>
              {t("budgets.dialog.person_off")}
            </Typography>
          )}

          {form.scope === "team" && (
            <TextField
              select
              size="small"
              label={t("budgets.dialog.team")}
              value={form.target_id}
              disabled={editing}
              onChange={(e) => set({ target_id: e.target.value })}
              {...check(
                "target_id",
                personReporting ? t("budgets.dialog.team_help_reporting") : t("budgets.dialog.team_help", { k: minGroupSize }),
              )}
              slotProps={{ htmlInput: { "data-budget-field": "team" } }}
              fullWidth
            >
              {teamRows.length === 0 && (
                <MenuItem value="" disabled>
                  {t("budgets.dialog.no_teams")}
                </MenuItem>
              )}
              {teamRows.map(({ team, depth }) => (
                <MenuItem key={team.id} value={team.id} sx={{ pl: 2 + (depth - 1) * 2 }}>
                  {team.name}
                </MenuItem>
              ))}
              {/* The team of a budget that is no longer in the list (archived): kept as it is. */}
              {editing && form.target_id && !teamRows.some((r) => r.team.id === form.target_id) && (
                <MenuItem value={form.target_id}>{budget?.target_label ?? t("budgets.list.removed_target")}</MenuItem>
              )}
            </TextField>
          )}

          {form.scope === "agent" && (
            <TextField
              select
              size="small"
              label={t("budgets.dialog.agent")}
              value={form.target_id}
              disabled={editing}
              onChange={(e) => set({ target_id: e.target.value })}
              {...check("target_id", t("budgets.dialog.agent_help"))}
              slotProps={{ htmlInput: { "data-budget-field": "agent" } }}
              fullWidth
            >
              {agents.length === 0 && (
                <MenuItem value="" disabled>
                  {t("budgets.dialog.no_agents")}
                </MenuItem>
              )}
              {agents.map((agent) => (
                <MenuItem key={agent.id} value={agent.id}>
                  {agent.name}
                </MenuItem>
              ))}
              {editing && form.target_id && !agents.some((a) => a.id === form.target_id) && (
                <MenuItem value={form.target_id}>{budget?.target_label ?? t("budgets.list.removed_target")}</MenuItem>
              )}
            </TextField>
          )}

          {form.scope === "person" &&
            (editing ? (
              <TextField
                size="small"
                label={t("budgets.dialog.person")}
                value={budget?.target_label ?? person?.email ?? t("budgets.list.a_person")}
                disabled
                fullWidth
              />
            ) : (
              <Autocomplete<AdminUser>
                options={users}
                value={person}
                onChange={(_, picked) => set({ target_id: picked?.id ?? "" })}
                getOptionLabel={(u) => u.email}
                isOptionEqualToValue={(a, b) => a.id === b.id}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    size="small"
                    label={t("budgets.dialog.person")}
                    {...check("target_id", t("budgets.dialog.person_help"))}
                  />
                )}
              />
            ))}

          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }} id="budget-unit-label">
              {t("budgets.dialog.unit")}
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={form.unit}
              onChange={(_, next: BudgetUnit | null) => next && set({ unit: next })}
              aria-labelledby="budget-unit-label"
            >
              <ToggleButton value="tokens" sx={{ textTransform: "none" }}>
                {t("budgets.unit.tokens")}
              </ToggleButton>
              <ToggleButton value="money" sx={{ textTransform: "none" }}>
                {t("budgets.unit.money")}
              </ToggleButton>
            </ToggleButtonGroup>
            {problemText("unit") && <FormHelperText error>{problemText("unit")}</FormHelperText>}
          </Box>

          <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
            <TextField
              size="small"
              label={t("budgets.dialog.amount")}
              value={form.amount}
              onChange={(e) => set({ amount: e.target.value })}
              {...check("amount", t(`budgets.dialog.amount_help_${form.unit}`))}
              slotProps={{ htmlInput: { inputMode: form.unit === "tokens" ? "numeric" : "decimal", "data-budget-field": "amount" } }}
              fullWidth
            />
            {form.unit === "money" && (
              <TextField
                size="small"
                label={t("budgets.dialog.currency")}
                value={form.currency}
                onChange={(e) => set({ currency: e.target.value.toUpperCase() })}
                {...check("currency", t("budgets.dialog.currency_help"))}
                slotProps={{
                  htmlInput: { maxLength: 3, spellCheck: false, list: "budget-currencies", "data-budget-field": "currency" },
                }}
                sx={{ width: { xs: "100%", sm: 220 }, flexShrink: 0 }}
              />
            )}
          </Stack>
          {form.unit === "money" && currencies.length > 0 && (
            <datalist id="budget-currencies">
              {currencies.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          )}

          <TextField
            select
            size="small"
            label={t("budgets.dialog.period")}
            value={form.period}
            onChange={(e) => set({ period: e.target.value as BudgetPeriod })}
            {...check("period", t("budgets.dialog.period_help"))}
            slotProps={{ htmlInput: { "data-budget-field": "period" } }}
            fullWidth
          >
            {BUDGET_PERIODS.map((period) => (
              <MenuItem key={period} value={period}>
                {t(`budgets.period.${period}`)}
              </MenuItem>
            ))}
          </TextField>

          <Box>
            <Stack direction="row" justifyContent="space-between" alignItems="baseline" gap={1}>
              <Typography variant="caption" color="text.secondary" id="budget-mode-label">
                {t("budgets.dialog.mode")}
              </Typography>
              <UsageHelpLink doc={BUDGETS_HELP.modes} />
            </Stack>
            <RadioGroup
              aria-labelledby="budget-mode-label"
              value={form.mode}
              onChange={(e) => set({ mode: e.target.value as BudgetMode })}
            >
              {BUDGET_MODES.map((mode) => (
                <FormControlLabel
                  key={mode}
                  value={mode}
                  control={<Radio size="small" />}
                  sx={{ alignItems: "flex-start", mb: 0.5, "& .MuiRadio-root": { pt: 0.25 } }}
                  label={
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {t(`budgets.mode.${mode}`)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {t(`budgets.mode_help.${mode}`)}
                      </Typography>
                    </Box>
                  }
                />
              ))}
            </RadioGroup>
            {problemText("mode") && <FormHelperText error>{problemText("mode")}</FormHelperText>}
          </Box>

          <TextField
            size="small"
            label={t("budgets.dialog.thresholds")}
            value={form.thresholds}
            onChange={(e) => set({ thresholds: e.target.value })}
            {...check("thresholds", t("budgets.dialog.thresholds_help"))}
            slotProps={{ htmlInput: { inputMode: "numeric", "data-budget-field": "thresholds" } }}
            fullWidth
          />

          <Autocomplete<AdminUser>
            options={users}
            value={owner}
            onChange={(_, picked) => set({ owner_id: picked?.id ?? "" })}
            getOptionLabel={(u) => u.email}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            renderInput={(params) => (
              <TextField
                {...params}
                size="small"
                label={t("budgets.dialog.owner")}
                {...check("owner_id", t("budgets.dialog.owner_help"))}
              />
            )}
          />

          {(problems.length > 0 || error) && (
            <Alert severity="error" data-budget-dialog-error>
              {error ?? t("budgets.dialog.problems")}
              {loose.map((p) => (
                <Typography key={`${p.field}-${p.code}`} variant="body2">
                  {codeText(p)}
                </Typography>
              ))}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {busy ? t("common.saving") : editing ? t("common.save") : t("common.create")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
