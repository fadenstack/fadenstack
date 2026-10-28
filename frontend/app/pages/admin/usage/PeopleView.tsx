/**
 * Per-person figures for a usage auditor (M02, M06, M07): shown only while
 * person-level reporting is on and the caller holds `usage.people:audit`
 * (the page asks the server, `can_view_people`).
 *
 * Nothing is fetched before a reason is given: the dialog asks whose figures
 * (one person or one team) and why, 10 to 500 characters, and only then calls
 * the server, which records the view with that reason. The rows come sorted by
 * name, then period, and stay so -- no column sorts by usage (M11).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import { adminGroups, adminUsers, type AdminUser, type Group } from "~/api/admin";
import { errorText } from "~/api/http";
import { REASON_MAX, REASON_MIN, reasonOk, usageApi, type PeopleUsage } from "~/api/usage";
import { WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { MeasuresTable } from "~/components/usage/UsageParts";
import { USAGE_HELP, UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { periodLabel, usageErrorText } from "~/components/usage/usageFormat";

type Scope = "person" | "team";

export default function PeopleView({ from, to }: { from: string; to: string }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<PeopleUsage | null>(null);

  return (
    <WidgetCard
      title={t("usage.admin.people.title")}
      subtitle={t("usage.admin.people.intro")}
      actions={
        <>
          <UsageHelpLink doc={USAGE_HELP.onePerson} />
          <Button variant="outlined" size="small" onClick={() => setOpen(true)}>
            {t("usage.admin.people.open")}
          </Button>
        </>
      }
    >
      {result ? (
        result.rows.length === 0 ? (
          <WidgetEmpty message={t("usage.admin.people.no_rows")} />
        ) : (
          <Stack gap={1}>
            <Typography variant="caption" color="text.secondary">
              {t("usage.admin.people.result_note", { granularity: t(`usage.granularity.${result.granularity}`) })}
            </Typography>
            <MeasuresTable
              ariaLabel={t("usage.admin.people.title")}
              labelHeader={t("usage.admin.people.col_person")}
              leadHeaders={[t("usage.admin.people.col_period")]}
              rows={result.rows.map((row, i) => ({
                key: `${row.person.id}-${row.period}-${i}`,
                label: (
                  <Stack component="span">
                    <Typography component="span" variant="body2" sx={{ fontWeight: 600 }}>
                      {row.person.name || row.person.email}
                    </Typography>
                    {/* Core keeps no names: the name is often the address itself. */}
                    {row.person.name && row.person.name !== row.person.email && (
                      <Typography component="span" variant="caption" color="text.secondary">
                        {row.person.email}
                      </Typography>
                    )}
                  </Stack>
                ),
                lead: [periodLabel(row.period, result.granularity, i18n.language)],
                measures: row,
              }))}
            />
          </Stack>
        )
      ) : (
        <Typography variant="body2" color="text.secondary">
          {t("usage.admin.people.none_yet")}
        </Typography>
      )}
      <PeopleDialog
        open={open}
        from={from}
        to={to}
        onClose={() => setOpen(false)}
        onResult={(answer) => {
          setResult(answer);
          setOpen(false);
        }}
      />
    </WidgetCard>
  );
}

function PeopleDialog({
  open,
  from,
  to,
  onClose,
  onResult,
}: {
  open: boolean;
  from: string;
  to: string;
  onClose: () => void;
  onResult: (answer: PeopleUsage) => void;
}) {
  const { t } = useTranslation();
  const [scope, setScope] = useState<Scope>("person");
  const [person, setPerson] = useState<AdminUser | null>(null);
  const [team, setTeam] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [teams, setTeams] = useState<Group[]>([]);
  const [listsError, setListsError] = useState<string | null>(null);

  // The people and teams to pick from, each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setListsError(null);
    Promise.all([adminUsers.list(), adminGroups.list()])
      .then(([u, g]) => {
        if (cancelled) return;
        setUsers(u);
        setTeams(g);
      })
      .catch((err: unknown) => {
        if (!cancelled) setListsError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const target = scope === "person" ? person?.id : team || undefined;
  const trimmed = reason.trim();
  const reasonError = trimmed.length > 0 && !reasonOk(reason);
  const canSubmit = Boolean(target) && reasonOk(reason) && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await usageApi.people({
        from,
        to,
        ...(scope === "person" ? { person: target } : { team: target }),
        reason: trimmed,
      });
      setReason("");
      onResult(answer);
    } catch (err) {
      setError(usageErrorText(err, (key) => t(key)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="usage-people-title"
    >
      <DialogTitle id="usage-people-title">{t("usage.admin.people.dialog_title")}</DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            {t("usage.admin.people.dialog_intro")}
          </Typography>
          {listsError && <Alert severity="error">{listsError}</Alert>}
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
              {t("usage.admin.people.scope_label")}
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={scope}
              onChange={(_, next: Scope | null) => next && setScope(next)}
              aria-label={t("usage.admin.people.scope_label")}
            >
              <ToggleButton value="person" sx={{ textTransform: "none" }}>
                {t("usage.admin.people.scope_person")}
              </ToggleButton>
              <ToggleButton value="team" sx={{ textTransform: "none" }}>
                {t("usage.admin.people.scope_team")}
              </ToggleButton>
            </ToggleButtonGroup>
          </Box>
          {scope === "person" ? (
            <Autocomplete<AdminUser>
              options={users}
              value={person}
              onChange={(_, picked) => setPerson(picked)}
              getOptionLabel={(u) => u.email}
              isOptionEqualToValue={(a, b) => a.id === b.id}
              renderInput={(params) => (
                <TextField {...params} size="small" label={t("usage.admin.people.person_label")} />
              )}
            />
          ) : (
            <TextField
              select
              size="small"
              label={t("usage.admin.people.team_label")}
              value={team}
              onChange={(e) => setTeam(e.target.value)}
            >
              {teams.map((g) => (
                <MenuItem key={g.id} value={g.id}>
                  {g.name}
                </MenuItem>
              ))}
            </TextField>
          )}
          <TextField
            label={t("usage.admin.people.reason_label")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            multiline
            minRows={2}
            required
            error={reasonError}
            helperText={
              reasonError
                ? t("usage.errors.reason_required")
                : t("usage.admin.people.reason_help", { min: REASON_MIN, max: REASON_MAX, length: trimmed.length })
            }
            slotProps={{ htmlInput: { maxLength: REASON_MAX + 50 } }}
          />
          {error && (
            <Alert severity="error" data-usage-people-error>
              {error}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button variant="contained" disabled={!canSubmit} onClick={() => void submit()}>
          {busy ? t("common.loading") : t("usage.admin.people.submit")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
