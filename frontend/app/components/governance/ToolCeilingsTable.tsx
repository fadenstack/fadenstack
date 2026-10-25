/**
 * Tool mode limits (with Enterprise): the most a chat's tool mode may be, for everyone, a team, an agent or a
 * person. Of the limits that apply to a chat the narrowest wins, and the chat says it is capped. Each change
 * applies at once on the server (within 30 seconds in every chat) and is audited.
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
import { principalKey, type PrincipalOption } from "~/api/principals";
import {
  EVERYONE,
  TOOL_MODES,
  toolCeilingsApi,
  type CeilingScope,
  type ToolCeiling,
  type ToolMode,
} from "~/api/toolCeilings";
import { AsyncSection } from "~/components/AsyncSection";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { formatDateTime } from "~/lib/dates";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

const SCOPES: CeilingScope[] = ["tenant", "team", "agent", "user"];

interface Target {
  id: string;
  label: string;
}

export function ToolCeilingsTable() {
  const { t } = useTranslation();
  const ceilings = useAsyncData(() => toolCeilingsApi.list(), [], { initialValue: [] as ToolCeiling[] });
  const agents = useAsyncData(async () => (await agentsApi.list()).map((a) => ({ id: a.id, label: a.name })), [], {
    initialValue: [] as Target[],
  });
  const [scope, setScope] = useState<CeilingScope>("tenant");
  const [target, setTarget] = useState<Target | null>(null);
  const [mode, setMode] = useState<ToolMode>("ask");
  const change = useAction();

  const taken = useMemo(() => new Set(ceilings.data.map((c) => `${c.scope}:${c.target_id}`)), [ceilings.data]);
  // The teams and people that have a limit already, as the picker names them: not offered again.
  const limited = useMemo(
    () =>
      new Set(
        ceilings.data
          .filter((c) => c.scope === "team" || c.scope === "user")
          .map((c) => principalKey(c.scope === "team" ? "group" : "user", c.target_id)),
      ),
    [ceilings.data],
  );
  const targetId = scope === "tenant" ? EVERYONE : target?.id;
  const canSet = Boolean(targetId) && !change.busy;
  const modeLabel = (m: ToolMode) => t(`tools.mode.${m}`);
  const reload = () => ceilings.refresh(true);

  const pickScope = (next: CeilingScope) => {
    setScope(next);
    setTarget(null);
  };
  const set = (s: CeilingScope, id: string, m: ToolMode, after?: () => void) =>
    void change.run(() => toolCeilingsApi.set(s, id, m), () => {
      after?.();
      return reload();
    });

  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        {t("governance.limits.intro")}
      </Typography>
      {change.error && <Alert severity="error">{change.error}</Alert>}

      <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ md: "center" }}>
        <TextField
          select
          size="small"
          label={t("governance.limits.for")}
          value={scope}
          onChange={(e) => pickScope(e.target.value as CeilingScope)}
          sx={{ minWidth: 160 }}
        >
          {SCOPES.map((s) => (
            <MenuItem key={s} value={s}>
              {t(`governance.limits.scope_${s}`)}
            </MenuItem>
          ))}
        </TextField>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {scope === "team" || scope === "user" ? (
            target ? (
              <Chip label={target.label} onDelete={() => setTarget(null)} />
            ) : (
              <PrincipalPicker
                search={(q) => agentsApi.findPrincipals(q)}
                types={[scope === "team" ? "group" : "user"]}
                exclude={limited}
                placeholder={t(scope === "team" ? "governance.limits.pick_team" : "governance.limits.pick_user")}
                onPick={(p: PrincipalOption) => setTarget({ id: p.id, label: p.label })}
              />
            )
          ) : scope === "agent" ? (
            <Autocomplete<Target>
              size="small"
              options={agents.data.filter((a) => !taken.has(`agent:${a.id}`))}
              value={target}
              getOptionLabel={(a) => a.label}
              isOptionEqualToValue={(a, b) => a.id === b.id}
              onChange={(_, next) => setTarget(next)}
              renderInput={(params) => <TextField {...params} label={t("governance.limits.pick_agent")} />}
            />
          ) : (
            <Typography variant="body2">{t("governance.limits.everyone_hint")}</Typography>
          )}
        </Box>
        <TextField
          select
          size="small"
          label={t("governance.limits.at_most")}
          value={mode}
          onChange={(e) => setMode(e.target.value as ToolMode)}
          sx={{ minWidth: 140 }}
        >
          {TOOL_MODES.map((m) => (
            <MenuItem key={m} value={m}>
              {modeLabel(m)}
            </MenuItem>
          ))}
        </TextField>
        <Button variant="contained" disabled={!canSet} onClick={() => targetId && set(scope, targetId, mode, () => setTarget(null))}>
          {t("governance.limits.set")}
        </Button>
      </Stack>

      <AsyncSection loading={ceilings.loading} error={ceilings.error} empty={ceilings.data.length === 0}>
        <List dense disablePadding>
          {ceilings.data.map((c) => {
            const who = c.label ?? t("governance.limits.scope_tenant");
            return (
              <ListItem
                key={`${c.scope}:${c.target_id}`}
                divider
                secondaryAction={
                  <IconButton
                    edge="end"
                    size="small"
                    aria-label={t("common.remove")}
                    disabled={change.busy}
                    onClick={() => void change.run(() => toolCeilingsApi.remove(c.scope, c.target_id), reload)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                }
              >
                <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, minWidth: 0, pr: 6 }}>
                  <Chip size="small" variant="outlined" label={t(`governance.limits.scope_${c.scope}`)} />
                  <ListItemText
                    primary={who}
                    secondary={t("governance.limits.changed", {
                      who: c.updated_by ?? "—",
                      when: formatDateTime(c.updated_at),
                    })}
                    slotProps={{ primary: { noWrap: true } }}
                  />
                  <TextField
                    select
                    size="small"
                    value={c.mode}
                    disabled={change.busy}
                    onChange={(e) => set(c.scope, c.target_id, e.target.value as ToolMode)}
                    slotProps={{ select: { SelectDisplayProps: { "aria-label": t("governance.limits.limit_of", { who }) } } }}
                    sx={{ minWidth: 130 }}
                  >
                    {TOOL_MODES.map((m) => (
                      <MenuItem key={m} value={m}>
                        {modeLabel(m)}
                      </MenuItem>
                    ))}
                  </TextField>
                </Stack>
              </ListItem>
            );
          })}
          {ceilings.data.length === 0 && (
            <ListItem>
              <ListItemText secondary={t("governance.limits.none")} />
            </ListItem>
          )}
        </List>
      </AsyncSection>
    </Stack>
  );
}
