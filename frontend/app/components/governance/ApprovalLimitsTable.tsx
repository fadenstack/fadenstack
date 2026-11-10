/**
 * Approval limits (with Enterprise): the longest a grant may last and what grants record, for everyone, a team, an
 * agent or a person; and the list of whom Bypass is for. Of the limits that apply to a grant the shortest longest
 * grant and the most recorded level win. A shorter limit ends running grants at their start plus the limit. Each
 * change applies at once on the server and is audited.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DeleteIcon from "@mui/icons-material/Delete";

import { agentsApi } from "~/api/agents";
import {
  approvalLimitsApi,
  EVIDENCE_LEVELS,
  LONGEST_MAX,
  LONGEST_MIN,
  type ApprovalLimits,
  type BypassListChange,
  type BypassSide,
  type EvidenceLevel,
  type LimitScope,
  type ListScope,
} from "~/api/approvalLimits";
import { principalKey, type PrincipalOption } from "~/api/principals";
import { EVERYONE } from "~/api/toolCeilings";
import { AsyncSection } from "~/components/AsyncSection";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { formatDateTime } from "~/lib/dates";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

const SCOPES: LimitScope[] = ["tenant", "team", "agent", "user"];
const LIST_SCOPES: ListScope[] = ["team", "agent", "user"];
const NO_LEVEL = "";
const BYPASS_SIDES: BypassSide[] = ["people", "agents"];
/** Persons and teams are the people side of the Bypass list; agents are the other. */
const sideOf = (scope: ListScope): BypassSide => (scope === "agent" ? "agents" : "people");

interface Target {
  id: string;
  label: string;
}

/** Whole minutes in the allowed range; null for an empty field, NaN for anything else. */
function parseMinutes(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  if (!Number.isInteger(n) || n < LONGEST_MIN || n > LONGEST_MAX) return Number.NaN;
  return n;
}

/** The picker for a team or a person, a chip once chosen, or the agent list. */
function TargetPicker({
  scope,
  target,
  onTarget,
  taken,
  agents,
}: {
  scope: LimitScope;
  target: Target | null;
  onTarget: (next: Target | null) => void;
  /** `scope:id` of those already in the list below, not offered again. */
  taken: Set<string>;
  agents: Target[];
}) {
  const { t } = useTranslation();
  const excluded = useMemo(
    () =>
      new Set(
        [...taken]
          .filter((k) => k.startsWith("team:") || k.startsWith("user:"))
          .map((k) => {
            const [kind, ...rest] = k.split(":");
            return principalKey(kind === "team" ? "group" : "user", rest.join(":"));
          }),
      ),
    [taken],
  );
  if (scope === "team" || scope === "user") {
    return target ? (
      <Chip label={target.label} onDelete={() => onTarget(null)} />
    ) : (
      <PrincipalPicker
        search={(q) => agentsApi.findPrincipals(q)}
        types={[scope === "team" ? "group" : "user"]}
        exclude={excluded}
        placeholder={t(scope === "team" ? "governance.limits.pick_team" : "governance.limits.pick_user")}
        onPick={(p: PrincipalOption) => onTarget({ id: p.id, label: p.label })}
      />
    );
  }
  if (scope === "agent") {
    return (
      <Autocomplete<Target>
        size="small"
        options={agents.filter((a) => !taken.has(`agent:${a.id}`))}
        value={target}
        getOptionLabel={(a) => a.label}
        isOptionEqualToValue={(a, b) => a.id === b.id}
        onChange={(_, next) => onTarget(next)}
        renderInput={(params) => <TextField {...params} label={t("governance.limits.pick_agent")} />}
      />
    );
  }
  return <Typography variant="body2">{t("governance.limits.everyone_hint")}</Typography>;
}

export function ApprovalLimitsTable() {
  const { t } = useTranslation();
  const state = useAsyncData(() => approvalLimitsApi.list(), [], {
    initialValue: { limits: [], bypass_for: [], bypass_sides: { people: false, agents: false } } as ApprovalLimits,
  });
  const agents = useAsyncData(async () => (await agentsApi.list()).map((a) => ({ id: a.id, label: a.name })), [], {
    initialValue: [] as Target[],
  });
  const reload = () => state.refresh(true);

  // The limits form.
  const [scope, setScope] = useState<LimitScope>("tenant");
  const [target, setTarget] = useState<Target | null>(null);
  const [minutes, setMinutes] = useState("");
  const [level, setLevel] = useState<EvidenceLevel | typeof NO_LEVEL>(NO_LEVEL);
  const [cut, setCut] = useState<number | null>(null);
  const change = useAction();

  // The Bypass list form.
  const [listScope, setListScope] = useState<ListScope>("team");
  const [listTarget, setListTarget] = useState<Target | null>(null);
  const bypass = useAction();
  const [ended, setEnded] = useState<number | null>(null);
  const [lifting, setLifting] = useState<BypassSide | null>(null);

  const takenLimits = useMemo(() => new Set(state.data.limits.map((l) => `${l.scope}:${l.target_id}`)), [state.data.limits]);
  const takenBypass = useMemo(
    () => new Set(state.data.bypass_for.map((b) => `${b.scope}:${b.target_id}`)),
    [state.data.bypass_for],
  );

  const parsed = parseMinutes(minutes);
  const minutesInvalid = Number.isNaN(parsed);
  const targetId = scope === "tenant" ? EVERYONE : target?.id;
  const canSet = Boolean(targetId) && !minutesInvalid && (parsed !== null || level !== NO_LEVEL) && !change.busy;

  const levelLabel = (l: EvidenceLevel) => t(`governance.approvals.level_${l}`);

  const submit = () => {
    if (!targetId) return;
    setCut(null);
    void change.run(
      () =>
        approvalLimitsApi.set(scope, targetId, {
          longest_minutes: parsed,
          evidence_level: level === NO_LEVEL ? null : level,
        }),
      (saved) => {
        setCut(saved.cut);
        setTarget(null);
        return reload();
      },
    );
  };

  const addToList = () => {
    if (!listTarget) return;
    setEnded(null);
    void bypass.run(
      () => approvalLimitsApi.addToBypassList(listScope, listTarget.id),
      (added) => {
        setEnded(added.ended);
        setListTarget(null);
        return reload();
      },
    );
  };

  /** Runs a change of the list, shows the sessions it ended, and reloads so the sides show their new state. */
  const changeList = (work: () => Promise<BypassListChange>) => {
    setEnded(null);
    return bypass.run(work, (changed) => {
      setEnded(changed.ended);
      return reload();
    });
  };
  const removeFromList = (s: ListScope, id: string) =>
    void changeList(() => approvalLimitsApi.removeFromBypassList(s, id));
  const restrict = (side: BypassSide) => void changeList(() => approvalLimitsApi.restrictSide(side));
  const confirmLift = () => {
    const side = lifting;
    if (!side) return;
    void changeList(() => approvalLimitsApi.liftSide(side)).then(() => setLifting(null));
  };

  return (
    <Stack spacing={3}>
      <Stack spacing={2}>
        <Typography variant="h6" component="h2">
          {t("governance.approvals.limits_title")}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {t("governance.approvals.limits_intro")}
        </Typography>
        {change.error && <Alert severity="error">{change.error}</Alert>}
        {cut !== null && cut > 0 && <Alert severity="info">{t("governance.approvals.cut", { count: cut })}</Alert>}

        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ md: "flex-start" }}>
          <TextField
            select
            size="small"
            label={t("governance.limits.for")}
            value={scope}
            onChange={(e) => {
              setScope(e.target.value as LimitScope);
              setTarget(null);
            }}
            sx={{ minWidth: 160 }}
          >
            {SCOPES.map((s) => (
              <MenuItem key={s} value={s}>
                {t(`governance.limits.scope_${s}`)}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ flex: 1, minWidth: 0, alignSelf: { md: "center" } }}>
            <TargetPicker
              scope={scope}
              target={target}
              onTarget={setTarget}
              taken={takenLimits}
              agents={agents.data}
            />
          </Box>
          <TextField
            size="small"
            type="number"
            label={t("governance.approvals.longest")}
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
            error={minutesInvalid}
            helperText={t("governance.approvals.longest_help", { min: LONGEST_MIN, max: LONGEST_MAX })}
            slotProps={{ htmlInput: { min: LONGEST_MIN, max: LONGEST_MAX, step: 1 } }}
            sx={{ minWidth: 200 }}
          />
          <TextField
            select
            size="small"
            label={t("governance.approvals.evidence")}
            value={level}
            onChange={(e) => setLevel(e.target.value as EvidenceLevel | typeof NO_LEVEL)}
            sx={{ minWidth: 220 }}
          >
            <MenuItem value={NO_LEVEL}>
              <em>{t("governance.approvals.level_none")}</em>
            </MenuItem>
            {EVIDENCE_LEVELS.map((l) => (
              <MenuItem key={l} value={l}>
                <Stack>
                  <span>{levelLabel(l)}</span>
                  <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "normal" }}>
                    {t(`governance.approvals.level_hint_${l}`)}
                  </Typography>
                </Stack>
              </MenuItem>
            ))}
          </TextField>
          <Button variant="contained" disabled={!canSet} onClick={submit}>
            {t("governance.approvals.set")}
          </Button>
        </Stack>

        <AsyncSection loading={state.loading} error={state.error} empty={false}>
          <List dense disablePadding>
            {state.data.limits.map((l) => (
              <ListItem
                key={`${l.scope}:${l.target_id}`}
                divider
                secondaryAction={
                  <IconButton
                    edge="end"
                    size="small"
                    aria-label={t("common.remove")}
                    disabled={change.busy}
                    onClick={() => void change.run(() => approvalLimitsApi.remove(l.scope, l.target_id), reload)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                }
              >
                <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, minWidth: 0, pr: 6 }}>
                  <Chip size="small" variant="outlined" label={t(`governance.limits.scope_${l.scope}`)} />
                  <ListItemText
                    primary={l.label ?? t("governance.limits.everyone_hint")}
                    secondary={t("governance.limits.changed", {
                      who: l.updated_by ?? "—",
                      when: formatDateTime(l.updated_at),
                    })}
                    slotProps={{ primary: { noWrap: true } }}
                  />
                  <Typography variant="body2" sx={{ whiteSpace: "nowrap" }}>
                    {l.longest_minutes === null
                      ? t("governance.approvals.longest_unset")
                      : t("governance.approvals.longest_value", { count: l.longest_minutes })}
                  </Typography>
                  <Typography variant="body2" sx={{ whiteSpace: "nowrap" }}>
                    {l.evidence_level === null
                      ? t("governance.approvals.level_unset")
                      : t("governance.approvals.level_value", { level: levelLabel(l.evidence_level) })}
                  </Typography>
                </Stack>
              </ListItem>
            ))}
            {state.data.limits.length === 0 && (
              <ListItem>
                <ListItemText secondary={t("governance.approvals.none")} />
              </ListItem>
            )}
          </List>
        </AsyncSection>
      </Stack>

      <Stack spacing={2}>
        <Typography variant="h6" component="h2">
          {t("governance.approvals.bypass_title")}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {t("governance.approvals.bypass_intro")}
        </Typography>
        {bypass.error && <Alert severity="error">{bypass.error}</Alert>}
        {ended !== null && ended > 0 && (
          <Alert severity="info">{t("governance.approvals.ended", { count: ended })}</Alert>
        )}

        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ md: "center" }}>
          <TextField
            select
            size="small"
            label={t("governance.approvals.bypass_for")}
            value={listScope}
            onChange={(e) => {
              setListScope(e.target.value as ListScope);
              setListTarget(null);
            }}
            sx={{ minWidth: 160 }}
          >
            {LIST_SCOPES.map((s) => (
              <MenuItem key={s} value={s}>
                {t(`governance.limits.scope_${s}`)}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <TargetPicker
              scope={listScope}
              target={listTarget}
              onTarget={setListTarget}
              taken={takenBypass}
              agents={agents.data}
            />
          </Box>
          <Button variant="contained" disabled={!listTarget || bypass.busy} onClick={addToList}>
            {t("governance.approvals.bypass_add")}
          </Button>
        </Stack>

        <AsyncSection loading={state.loading} error={state.error} empty={false}>
          <Stack spacing={2}>
            {BYPASS_SIDES.map((side) => {
              const restricted = state.data.bypass_sides[side];
              const entries = state.data.bypass_for.filter((b) => sideOf(b.scope) === side);
              return (
                <Stack key={side} spacing={1} data-testid={`bypass-side-${side}`}>
                  <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                    <Typography variant="subtitle2" component="h3">
                      {t(`governance.approvals.bypass_side_${side}`)}
                    </Typography>
                    {restricted ? (
                      <Button size="small" disabled={bypass.busy} onClick={() => setLifting(side)}>
                        {t(`governance.approvals.bypass_lift_${side}`)}
                      </Button>
                    ) : (
                      <Button size="small" color="inherit" disabled={bypass.busy} onClick={() => restrict(side)}>
                        {t(`governance.approvals.bypass_restrict_${side}`)}
                      </Button>
                    )}
                  </Stack>
                  {!restricted && (
                    <Typography variant="body2" color="text.secondary">
                      {t(`governance.approvals.bypass_${side}_open`)}
                    </Typography>
                  )}
                  {restricted && entries.length === 0 && (
                    <Alert severity="warning">{t(`governance.approvals.bypass_${side}_empty`)}</Alert>
                  )}
                  {restricted && entries.length > 0 && (
                    <List dense disablePadding>
                      {entries.map((b) => (
                        <ListItem
                          key={`${b.scope}:${b.target_id}`}
                          divider
                          secondaryAction={
                            <IconButton
                              edge="end"
                              size="small"
                              aria-label={t("common.remove")}
                              disabled={bypass.busy}
                              onClick={() => removeFromList(b.scope, b.target_id)}
                            >
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          }
                        >
                          <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, minWidth: 0, pr: 6 }}>
                            <Chip size="small" variant="outlined" label={t(`governance.limits.scope_${b.scope}`)} />
                            <ListItemText
                              primary={b.label ?? b.target_id}
                              secondary={t("governance.approvals.added", {
                                who: b.created_by ?? "—",
                                when: formatDateTime(b.created_at),
                              })}
                              slotProps={{ primary: { noWrap: true } }}
                            />
                          </Stack>
                        </ListItem>
                      ))}
                    </List>
                  )}
                </Stack>
              );
            })}
          </Stack>
        </AsyncSection>
      </Stack>

      <ConfirmDialog
        open={lifting !== null}
        title={t("governance.approvals.bypass_lift_title")}
        message={lifting ? t(`governance.approvals.bypass_lift_body_${lifting}`) : ""}
        confirmLabel={lifting ? t(`governance.approvals.bypass_lift_${lifting}`) : ""}
        confirmColor="primary"
        cancelLabel={t("common.cancel")}
        loading={bypass.busy}
        onConfirm={confirmLift}
        onClose={() => setLifting(null)}
      />
    </Stack>
  );
}
