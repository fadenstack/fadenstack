/**
 * Per-person figures for a usage auditor (M02, M06, M07): shown only while
 * person-level reporting is on and the caller holds `usage.people:audit`
 * (the page asks the server, `can_view_people`).
 *
 * Nothing is fetched before a reason is given: the dialog asks whose figures
 * (one person or one team) and why, 10 to 500 characters, and only then calls
 * the server, which records the view with that reason. Whom to pick comes from
 * the server under the same gate (`/people/choices`): a usage auditor need not
 * be an administrator. The rows come sorted by name, then period, and stay so
 * -- no column sorts by usage (M11). One person of the view can then be
 * downloaded (JSON, CSV) under the same reason, each download recorded by the
 * server too (M07, M13). The reason travels as a header, never in an address.
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
import DownloadIcon from "@mui/icons-material/Download";

import { REASON_MAX, REASON_MIN, reasonOk, usageApi, type PeopleChoices, type PeopleUsage } from "~/api/usage";
import { WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { MeasuresTable } from "~/components/usage/UsageParts";
import { USAGE_HELP, UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { periodLabel, usageErrorText } from "~/components/usage/usageFormat";
import { saveFile } from "~/lib/saveFile";
import { pickableTeams } from "~/lib/teamTree";

type Scope = "person" | "team";
type Choice = PeopleChoices["people"][number];

interface Someone {
  id: string;
  label: string;
}

/** A view that was granted: its rows, the reason it was given for, and whose data may be downloaded. */
interface Shown {
  answer: PeopleUsage;
  reason: string;
  people: Someone[];
}

/** The people of a view, by first appearance (the rows come sorted by name); the chosen person too. */
function peopleOf(answer: PeopleUsage, chosen: Someone | null): Someone[] {
  const seen = new Map<string, Someone>();
  for (const row of answer.rows) {
    if (!seen.has(row.person.id)) seen.set(row.person.id, { id: row.person.id, label: row.person.name || row.person.email });
  }
  if (chosen && !seen.has(chosen.id)) seen.set(chosen.id, chosen);
  return [...seen.values()];
}

export default function PeopleView({ from, to }: { from: string; to: string }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<Shown | null>(null);
  const result = shown?.answer ?? null;

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
      {shown && shown.people.length > 0 && (
        <PersonExport key={shown.people.map((p) => p.id).join(",")} people={shown.people} reason={shown.reason} />
      )}
      <PeopleDialog
        open={open}
        from={from}
        to={to}
        onClose={() => setOpen(false)}
        onResult={(answer, reason, chosen) => {
          setShown({ answer, reason, people: peopleOf(answer, chosen) });
          setOpen(false);
        }}
      />
    </WidgetCard>
  );
}

/**
 * Download one person's data, under the reason the view was given for: the
 * server checks it again and records each download (M07). Fetched with the
 * reason as a header (a link would have to put it in the address) and saved
 * from the page; nothing is fetched until a button is used.
 */
function PersonExport({ people, reason }: { people: Someone[]; reason: string }) {
  const { t } = useTranslation();
  const [id, setId] = useState(people[0].id);
  const [busy, setBusy] = useState<"json" | "csv" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(format: "json" | "csv") {
    setBusy(format);
    setError(null);
    try {
      const file = await usageApi.peopleExport(id, format, reason);
      saveFile(file.blob, file.filename);
    } catch (err) {
      setError(usageErrorText(err, (key) => t(key)));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Stack gap={1} sx={{ mt: 2 }} data-usage-person-export>
      <Typography variant="subtitle2" component="h3">
        {t("usage.admin.people.export_title")}
      </Typography>
      <Stack direction={{ xs: "column", sm: "row" }} gap={1} alignItems={{ sm: "center" }}>
        <TextField
          select
          size="small"
          label={t("usage.admin.people.person_label")}
          value={id}
          onChange={(e) => setId(e.target.value)}
          sx={{ minWidth: 240 }}
        >
          {people.map((p) => (
            <MenuItem key={p.id} value={p.id}>
              {p.label}
            </MenuItem>
          ))}
        </TextField>
        <Button
          variant="outlined"
          size="small"
          startIcon={<DownloadIcon />}
          disabled={busy !== null}
          onClick={() => void download("json")}
        >
          {busy === "json" ? t("common.loading") : t("usage.my.export_json")}
        </Button>
        <Button
          variant="outlined"
          size="small"
          startIcon={<DownloadIcon />}
          disabled={busy !== null}
          onClick={() => void download("csv")}
        >
          {busy === "csv" ? t("common.loading") : t("usage.my.export_csv")}
        </Button>
      </Stack>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} data-usage-export-error>
          {error}
        </Alert>
      )}
      <Typography variant="caption" color="text.secondary">
        {t("usage.admin.people.export_note")}
      </Typography>
    </Stack>
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
  /** The answer, the reason it was granted for, and the person chosen (none for a team). */
  onResult: (answer: PeopleUsage, reason: string, chosen: Someone | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const [scope, setScope] = useState<Scope>("person");
  const [person, setPerson] = useState<Choice | null>(null);
  const [team, setTeam] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [choices, setChoices] = useState<PeopleChoices>({ people: [], teams: [] });
  const [listsError, setListsError] = useState<string | null>(null);

  // The people and teams to pick from, each time the dialog opens: from the usage API, under the same
  // gate as the view (a usage auditor may not read the administrators' lists of users and teams).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setListsError(null);
    usageApi
      .peopleChoices()
      .then((found) => {
        if (!cancelled) setChoices({ ...found, people: found.people ?? [], teams: found.teams ?? [] });
      })
      .catch((err: unknown) => {
        if (!cancelled) setListsError(usageErrorText(err, (key) => t(key)));
      });
    return () => {
      cancelled = true;
    };
  }, [open, t]);

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
      onResult(answer, trimmed, scope === "person" && person ? { id: person.id, label: person.email } : null);
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
            <Autocomplete<Choice>
              options={choices.people}
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
              {pickableTeams(choices.teams, i18n.language).map(({ team: g, depth }) => (
                <MenuItem key={g.id} value={g.id} sx={{ pl: 2 + (depth - 1) * 2 }}>
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
