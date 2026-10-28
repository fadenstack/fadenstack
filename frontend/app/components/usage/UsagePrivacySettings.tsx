/**
 * Settings > Usage and privacy: who sees usage figures, how fine and how long
 * they are kept, the instance's limits, and the configuration statement.
 *
 * - Person-level reporting (M02) is off by default; the switch says what
 *   turning it on means and that a works council has to agree where there is
 *   one (§ 87(1) no. 6 BetrVG). It is a protected setting: changing it needs
 *   root mode, and the switch stays locked without it.
 * - k (M04), per-person granularity (M10) and the retention periods (M09) are
 *   checked against the backend's ranges before anything is sent; the server
 *   checks again and names the fields it rejects. Keeping per-person figures
 *   past 90 days earns a warning (S06).
 * - The instance's limits (requests and tokens per minute, tokens per day) are
 *   counted per instance, without anyone's name (S11).
 * - The configuration statement (S09) is a download of the current settings
 *   for the works-agreement annex and the DPIA, in a chosen language.
 */
import { useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DownloadIcon from "@mui/icons-material/Download";

import {
  PERSON_RETENTION_WARN_DAYS,
  USAGE_SETTING_RANGES,
  UsageProblemsError,
  usageApi,
  type Granularity,
  type RangedSetting,
  type UsageLimits,
  type UsageSettings,
} from "~/api/usage";
import { useAsyncData } from "~/lib/useAsyncData";

import { USAGE_HELP, UsageHelpLink } from "./UsageHelpLink";
import { usageErrorText } from "./usageFormat";

/** The languages the statement is written in. */
export const STATEMENT_LANGUAGES = ["en", "de", "es", "fr", "zh"] as const;

const RANGED: RangedSetting[] = [
  "min_group_size",
  "request_log_retention_days",
  "person_retention_days",
  "hourly_retention_days",
  "daily_retention_months",
];

const LIMIT_KEYS: (keyof UsageLimits)[] = ["rpm_limit", "tpm_limit", "tokens_per_day"];

interface Draft {
  person_reporting: boolean;
  person_granularity: Granularity;
  time_zone: string;
  ranged: Record<RangedSetting, string>;
}

function toDraft(s: UsageSettings): Draft {
  return {
    person_reporting: s.person_reporting,
    person_granularity: s.person_granularity,
    time_zone: s.time_zone,
    ranged: Object.fromEntries(RANGED.map((k) => [k, String(s[k])])) as Record<RangedSetting, string>,
  };
}

/** A whole number within the setting's range, else null. */
export function rangedValue(key: RangedSetting, raw: string): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number(raw.trim());
  const { min, max } = USAGE_SETTING_RANGES[key];
  return n >= min && n <= max ? n : null;
}

/** An IANA time zone this browser knows (the server checks it again). */
export function validTimeZone(zone: string): boolean {
  if (!zone.trim()) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone.trim() });
    return true;
  } catch {
    return false;
  }
}

/** A limit: empty for none, else a whole number above 0; undefined when neither. */
function limitValue(raw: string): number | null | undefined {
  const text = raw.trim();
  if (!text) return null;
  if (!/^\d+$/.test(text)) return undefined;
  const n = Number(text);
  return n > 0 ? n : undefined;
}

/** A language's own name ("Deutsch"), from the browser; the code when it has none. */
function languageName(code: string): string {
  try {
    return new Intl.DisplayNames([code], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** A section's heading, with a link to the Help section that explains it. */
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

export default function UsagePrivacySettings() {
  return (
    <Stack spacing={2} sx={{ maxWidth: 880 }}>
      <PrivacySection />
      <LimitsSection />
      <StatementSection />
    </Stack>
  );
}

function PrivacySection() {
  const { t } = useTranslation();
  const rootModeActive = useOutletContext<{ rootModeActive?: boolean } | undefined>()?.rootModeActive ?? false;
  const loaded = useAsyncData(() => usageApi.settings(), []);
  const [saved, setSaved] = useState<UsageSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loaded.data) {
      setSaved(loaded.data);
      setDraft(toDraft(loaded.data));
    }
  }, [loaded.data]);

  const changes = useMemo<Partial<UsageSettings> | null>(() => {
    if (!saved || !draft) return null;
    const out: Partial<UsageSettings> = {};
    if (draft.person_reporting !== saved.person_reporting) out.person_reporting = draft.person_reporting;
    if (draft.person_granularity !== saved.person_granularity) out.person_granularity = draft.person_granularity;
    if (draft.time_zone.trim() !== saved.time_zone) out.time_zone = draft.time_zone.trim();
    for (const key of RANGED) {
      const value = rangedValue(key, draft.ranged[key]);
      if (value !== null && value !== saved[key]) out[key] = value;
    }
    return out;
  }, [saved, draft]);

  if (loaded.loading && !draft) return <CircularProgress size={20} aria-label={t("common.loading")} />;
  if (!draft || !saved) {
    return <Alert severity="error">{loaded.error ?? t("common.load_failed")}</Alert>;
  }

  // A zone is checked once it is changed: one the server holds stays savable.
  const zoneBad = draft.time_zone.trim() !== saved.time_zone && !validTimeZone(draft.time_zone);
  const invalid = RANGED.some((k) => rangedValue(k, draft.ranged[k]) === null) || zoneBad;
  const dirty = Boolean(changes && Object.keys(changes).length > 0);
  const personDays = rangedValue("person_retention_days", draft.ranged.person_retention_days);

  function setRanged(key: RangedSetting, raw: string) {
    setDraft((d) => (d ? { ...d, ranged: { ...d.ranged, [key]: raw } } : d));
    setProblems((p) => ({ ...p, [key]: "" }));
    setStatus(null);
  }

  async function save() {
    if (!changes || invalid) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    setProblems({});
    try {
      const next = await usageApi.updateSettings(changes);
      setSaved(next);
      setDraft(toDraft(next));
      setStatus(t("usage.settings.saved"));
    } catch (err) {
      if (err instanceof UsageProblemsError) {
        setProblems(
          Object.fromEntries(err.problems.map((p) => [p.field, t(`usage.errors.${p.code}`, { defaultValue: p.code })])),
        );
      } else {
        setError(usageErrorText(err, (key) => t(key)));
      }
    } finally {
      setBusy(false);
    }
  }

  const rangedField = (key: RangedSetting) => {
    const { min, max } = USAGE_SETTING_RANGES[key];
    const bad = rangedValue(key, draft.ranged[key]) === null;
    const warn = key === "person_retention_days" && personDays !== null && personDays > PERSON_RETENTION_WARN_DAYS;
    return (
      <TextField
        key={key}
        size="small"
        type="number"
        label={t(`usage.settings.${key}`)}
        value={draft.ranged[key]}
        onChange={(e) => setRanged(key, e.target.value)}
        error={bad || Boolean(problems[key])}
        helperText={
          problems[key] ||
          (bad
            ? t("usage.settings.range_error", { min, max })
            : warn
              ? t("usage.settings.person_retention_warning", { days: PERSON_RETENTION_WARN_DAYS })
              : t(`usage.settings.${key}_help`, { min, max }))
        }
        slotProps={{
          htmlInput: { min, max, step: 1, "data-usage-setting": key },
          formHelperText: warn && !bad ? { sx: { color: "warning.main" } } : undefined,
        }}
        fullWidth
      />
    );
  };

  return (
    <Paper sx={{ p: 2 }}>
      <SectionTitle title={t("usage.settings.privacy_title")} doc={USAGE_HELP.settings} />
      <Typography variant="body2" color="text.secondary">
        {t("usage.settings.privacy_intro")}
      </Typography>
      <Divider sx={{ my: 1.5 }} />

      <Stack spacing={2}>
        <Box>
          <FormControlLabel
            control={
              <Switch
                checked={draft.person_reporting}
                disabled={!rootModeActive || busy}
                onChange={(e) => {
                  setDraft({ ...draft, person_reporting: e.target.checked });
                  setStatus(null);
                }}
                slotProps={{ input: { role: "switch", "aria-label": t("usage.settings.person_reporting") } }}
              />
            }
            label={
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t("usage.settings.person_reporting")}
              </Typography>
            }
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {draft.person_reporting ? t("usage.settings.person_reporting_on_text") : t("usage.settings.person_reporting_off_text")}
          </Typography>
          <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
            {t("usage.settings.works_council")}
          </Alert>
          <Typography variant="caption" color={rootModeActive ? "text.secondary" : "warning.main"} sx={{ display: "block", mt: 0.75 }} data-usage-root-hint>
            {rootModeActive ? t("usage.settings.root_mode_hint") : t("usage.settings.root_mode_needed")}
          </Typography>
        </Box>

        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "repeat(2, minmax(0, 1fr))" } }}>
          {rangedField("min_group_size")}
          <TextField
            select
            size="small"
            label={t("usage.settings.person_granularity")}
            value={draft.person_granularity}
            onChange={(e) => setDraft({ ...draft, person_granularity: e.target.value as Granularity })}
            helperText={problems.person_granularity || t("usage.settings.person_granularity_help")}
            error={Boolean(problems.person_granularity)}
            fullWidth
          >
            <MenuItem value="day">{t("usage.settings.granularity_day")}</MenuItem>
            <MenuItem value="month">{t("usage.settings.granularity_month")}</MenuItem>
          </TextField>
          {rangedField("request_log_retention_days")}
          {rangedField("person_retention_days")}
          {rangedField("hourly_retention_days")}
          {rangedField("daily_retention_months")}
          <TextField
            size="small"
            label={t("usage.settings.time_zone")}
            value={draft.time_zone}
            onChange={(e) => {
              setDraft({ ...draft, time_zone: e.target.value });
              setProblems((p) => ({ ...p, time_zone: "" }));
            }}
            error={zoneBad || Boolean(problems.time_zone)}
            helperText={
              problems.time_zone || (zoneBad ? t("usage.settings.time_zone_error") : t("usage.settings.time_zone_help"))
            }
            fullWidth
          />
        </Box>

        {error && <Alert severity="error">{error}</Alert>}
        {status && <Alert severity="success">{status}</Alert>}

        <Box>
          <Button variant="contained" disabled={!dirty || invalid || busy} onClick={() => void save()}>
            {busy ? t("common.saving") : t("common.save")}
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}

function LimitsSection() {
  const { t } = useTranslation();
  const loaded = useAsyncData(() => usageApi.limits(), []);
  const [draft, setDraft] = useState<Record<keyof UsageLimits, string> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loaded.data) {
      setDraft(
        Object.fromEntries(LIMIT_KEYS.map((k) => [k, loaded.data[k] == null ? "" : String(loaded.data[k])])) as Record<
          keyof UsageLimits,
          string
        >,
      );
    }
  }, [loaded.data]);

  if (loaded.loading && !draft) return <CircularProgress size={20} aria-label={t("common.loading")} />;
  if (!draft) return <Alert severity="error">{loaded.error ?? t("common.load_failed")}</Alert>;

  const values = Object.fromEntries(LIMIT_KEYS.map((k) => [k, limitValue(draft[k])])) as Record<
    keyof UsageLimits,
    number | null | undefined
  >;
  const invalid = LIMIT_KEYS.some((k) => values[k] === undefined);

  async function save() {
    if (invalid) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await usageApi.updateLimits(values as UsageLimits);
      setStatus(t("usage.settings.saved"));
      await loaded.refresh(true);
    } catch (err) {
      // A limit the server refuses breaks the one rule all three share.
      setError(err instanceof UsageProblemsError ? t("usage.settings.caps_error") : usageErrorText(err, (key) => t(key)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper sx={{ p: 2 }}>
      <SectionTitle title={t("usage.settings.caps_title")} doc={USAGE_HELP.limits} />
      <Typography variant="body2" color="text.secondary">
        {t("usage.settings.caps_intro")}
      </Typography>
      <Divider sx={{ my: 1.5 }} />
      <Stack spacing={2}>
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "minmax(0, 1fr)", sm: "repeat(3, minmax(0, 1fr))" } }}>
          {LIMIT_KEYS.map((key) => (
            <TextField
              key={key}
              size="small"
              type="number"
              label={t(`usage.settings.${key}`)}
              value={draft[key]}
              placeholder={t("usage.settings.no_limit")}
              onChange={(e) => {
                setDraft({ ...draft, [key]: e.target.value });
                setStatus(null);
              }}
              error={values[key] === undefined}
              helperText={values[key] === undefined ? t("usage.settings.caps_error") : t("usage.settings.caps_help")}
              slotProps={{ htmlInput: { min: 1, step: 1, "data-usage-limit": key } }}
              fullWidth
            />
          ))}
        </Box>
        {error && <Alert severity="error">{error}</Alert>}
        {status && <Alert severity="success">{status}</Alert>}
        <Box>
          <Button variant="contained" disabled={invalid || busy} onClick={() => void save()}>
            {busy ? t("common.saving") : t("common.save")}
          </Button>
        </Box>
      </Stack>
    </Paper>
  );
}

function StatementSection() {
  const { t, i18n } = useTranslation();
  const uiLanguage = (i18n.resolvedLanguage ?? i18n.language ?? "en").slice(0, 2);
  const [language, setLanguage] = useState<string>(
    (STATEMENT_LANGUAGES as readonly string[]).includes(uiLanguage) ? uiLanguage : "en",
  );
  return (
    <Paper sx={{ p: 2 }}>
      <SectionTitle title={t("usage.settings.statement_title")} doc={USAGE_HELP.documents} />
      <Typography variant="body2" color="text.secondary">
        {t("usage.settings.statement_intro")}
      </Typography>
      <Divider sx={{ my: 1.5 }} />
      <Stack direction={{ xs: "column", sm: "row" }} gap={1.5} alignItems={{ sm: "center" }}>
        <TextField
          select
          size="small"
          label={t("usage.settings.statement_language")}
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          sx={{ minWidth: 200 }}
        >
          {STATEMENT_LANGUAGES.map((code) => (
            <MenuItem key={code} value={code}>
              {languageName(code)}
            </MenuItem>
          ))}
        </TextField>
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          component="a"
          href={usageApi.statementUrl(language)}
          download
        >
          {t("usage.settings.statement_download")}
        </Button>
      </Stack>
    </Paper>
  );
}
