/**
 * Settings > Mail: how this server sends mail (password resets, sign-in codes, alerts for administrators)
 * through the organisation's mail server. One form, one Save; a test mail with the form's values, saved or
 * not; and whether mail goes out, and if not why. Shown while the server offers `mail_settings` (Enterprise).
 *
 * Secrets are never shown: a stored one leaves its field empty, and typing replaces it. A stored secret
 * belongs to the server (host:port) it was saved for: a form pointing elsewhere must bring it again.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import InputAdornment from "@mui/material/InputAdornment";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LockIcon from "@mui/icons-material/Lock";
import SaveIcon from "@mui/icons-material/Save";
import SendIcon from "@mui/icons-material/Send";

import { errorText } from "~/api/http";
import {
  mailApi,
  mailProblems,
  testRunning,
  tooManyTests,
  type MailAuth,
  type MailSecurity,
  type MailTestResult,
  type MailView,
} from "~/api/mailSettings";
import { formatDateTime } from "~/lib/dates";
import { helpUrl } from "~/lib/pageHelp";

import { FailureText, MailStatePanel } from "./MailState";
import {
  byField,
  formFromSettings,
  isAddress,
  localProblems,
  MAX_LENGTH,
  replacedServer,
  serverOf,
  toBody,
  withAuth,
  withSecurity,
  type MailFormState,
} from "./model";
import { RecipientsField } from "./RecipientsField";

const SECURITY: MailSecurity[] = ["starttls", "tls"];
const AUTH: MailAuth[] = ["none", "password", "microsoft365"];

interface AdminContext {
  userEmail?: string;
  rootModeActive?: boolean;
}

type SaveOutcome = { kind: "saved" } | { kind: "problems"; rootMode: boolean } | { kind: "error"; message: string };
type TestOutcome =
  | { kind: "result"; result: MailTestResult; to: string }
  | { kind: "problems" }
  | { kind: "too_many" }
  | { kind: "running" }
  | { kind: "error"; message: string };

function Section({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="subtitle1" component="h2">
        {title}
      </Typography>
      {intro && (
        <Typography variant="body2" color="text.secondary">
          {intro}
        </Typography>
      )}
      <Divider sx={{ my: 1.5 }} />
      <Stack spacing={2}>{children}</Stack>
    </Paper>
  );
}

function TestOutcomeView({ outcome }: { outcome: TestOutcome }) {
  const { t } = useTranslation();
  if (outcome.kind === "too_many" || outcome.kind === "running") {
    return (
      <Alert severity="warning" data-testid="mail-test-result">
        {outcome.kind === "running" ? t("mail.test.running") : t("mail.test.too_many")}
      </Alert>
    );
  }
  if (outcome.kind === "problems") {
    return (
      <Alert severity="error" data-testid="mail-test-result">
        {t("mail.save.problems")}
      </Alert>
    );
  }
  if (outcome.kind === "error") {
    return (
      <Alert severity="error" data-testid="mail-test-result">
        {outcome.message}
      </Alert>
    );
  }
  const { result, to } = outcome;
  if (!result.ok) {
    return (
      <Alert severity="error" data-testid="mail-test-result" sx={{ "& .MuiAlert-message": { minWidth: 0, flexGrow: 1 } }}>
        <AlertTitle>{t("mail.test.failed")}</AlertTitle>
        <FailureText code={result.code ?? "server_error"} reason={result.reason} />
      </Alert>
    );
  }
  const details = [
    result.tls_version ? t("mail.test.tls", { version: result.tls_version }) : "",
    result.auth ? t("mail.test.auth", { auth: t(`mail.sign_in.auth_${result.auth}`) }) : "",
    result.elapsed_ms !== null ? t("mail.test.elapsed", { ms: result.elapsed_ms }) : "",
  ].filter(Boolean);
  return (
    <Alert severity="success" data-testid="mail-test-result" sx={{ "& .MuiAlert-message": { minWidth: 0 } }}>
      <AlertTitle sx={{ overflowWrap: "anywhere" }}>{t("mail.test.sent", { to })}</AlertTitle>
      {details.join(" · ")}
    </Alert>
  );
}

export default function MailSettingsTab() {
  const { t, i18n } = useTranslation();
  const context = useOutletContext<AdminContext | undefined>();
  const [view, setView] = useState<MailView | null>(null);
  const [form, setForm] = useState<MailFormState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveOutcome, setSaveOutcome] = useState<SaveOutcome | null>(null);
  const [to, setTo] = useState(context?.userEmail ?? "");
  const [toTouched, setToTouched] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testOutcome, setTestOutcome] = useState<TestOutcome | null>(null);
  // The server that switching to Microsoft 365 replaced, to say so until the server is edited.
  const [m365Replaced, setM365Replaced] = useState<string | null>(null);

  async function load() {
    setLoadError(null);
    try {
      const next = await mailApi.get();
      setView(next);
      setForm(formFromSettings(next.settings));
    } catch (err) {
      setLoadError(errorText(err) || t("common.load_failed"));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // The signed-in administrator's own address, once the layout knows it, unless one was typed.
  useEffect(() => {
    if (!toTouched && context?.userEmail) setTo(context.userEmail);
  }, [context?.userEmail, toTouched]);

  /** Asks again whether mail goes out; the form keeps what was typed. */
  async function refreshState() {
    setRefreshing(true);
    try {
      setView(await mailApi.get());
    } catch (err) {
      setLoadError(errorText(err) || t("common.load_failed"));
    } finally {
      setRefreshing(false);
    }
  }

  if (!view || !form) {
    return loadError ? (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={() => void load()}>
            {t("common.refresh")}
          </Button>
        }
      >
        {loadError}
      </Alert>
    ) : (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  const stored = view.settings;
  // The layout's polled state when it gives one (root mode ends while the page is open); else the server's.
  const rootMode = typeof context?.rootModeActive === "boolean" ? context.rootModeActive : view.root_mode;
  const helpPage = helpUrl("administer/mail", i18n.language);
  // The stored secrets go only to the server they were saved for (the server checks the same).
  const otherServer = serverOf(form) !== stored.secrets_server;

  function set(change: Partial<MailFormState>) {
    setForm((prev) => (prev ? { ...prev, ...change } : prev));
    setProblems((prev) => {
      const fields = Object.keys(change).filter((field) => field in prev);
      if (!fields.length) return prev;
      const next = { ...prev };
      for (const field of fields) delete next[field];
      return next;
    });
    setSaveOutcome((prev) => (prev?.kind === "saved" ? null : prev));
  }

  /** The problem with *field* in words: the field's own wording when it has one. */
  function problemText(field: string): string | undefined {
    const code = problems[field];
    if (!code) return undefined;
    for (const key of [`mail.problems.${field}_${code}`, `mail.problems.${code}`]) {
      if (i18n.exists(key)) return t(key);
    }
    return t("mail.problems.invalid");
  }

  /** `error` and `helperText` for *field*: its problem, or else its help. */
  function check(field: string, help?: string) {
    const problem = problemText(field);
    return { error: !!problem, helperText: problem ?? help };
  }

  async function save() {
    if (!form) return;
    const local = localProblems(form);
    if (local.length) {
      setProblems(byField(local));
      setSaveOutcome({ kind: "problems", rootMode: false });
      return;
    }
    setSaving(true);
    setSaveOutcome(null);
    try {
      const next = await mailApi.save(toBody(form));
      setView(next);
      setForm(formFromSettings(next.settings));
      setProblems({});
      setSaveOutcome({ kind: "saved" });
    } catch (err) {
      const found = mailProblems(err);
      if (found.length) {
        setProblems(byField(found));
        setSaveOutcome({ kind: "problems", rootMode: found.some((p) => p.code === "root_mode") });
      } else {
        setSaveOutcome({ kind: "error", message: errorText(err) || t("common.save_failed") });
      }
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    if (!form) return;
    const local = localProblems(form);
    if (!isAddress(to)) local.push({ field: "to", code: to.trim() ? "invalid" : "required" });
    if (local.length) {
      setProblems((prev) => ({ ...prev, ...byField(local) }));
      setTestOutcome({ kind: "problems" });
      return;
    }
    setTesting(true);
    setTestOutcome(null);
    try {
      const result = await mailApi.test(to.trim(), toBody(form));
      // The form passed the server's checks: what they found before is gone (root mode is not checked here).
      setProblems((prev) => Object.fromEntries(Object.entries(prev).filter(([, code]) => code === "root_mode")));
      setTestOutcome({ kind: "result", result, to: to.trim() });
    } catch (err) {
      const found = mailProblems(err);
      if (found.length) {
        setProblems(byField(found));
        setTestOutcome({ kind: "problems" });
      } else if (testRunning(err)) {
        setTestOutcome({ kind: "running" });
      } else if (tooManyTests(err)) {
        setTestOutcome({ kind: "too_many" });
      } else {
        setTestOutcome({ kind: "error", message: errorText(err) });
      }
    } finally {
      setTesting(false);
    }
  }

  /** A secret's field: empty with a lock while one is stored; typing replaces it. */
  function secretField(field: "password" | "m365_client_secret", label: string, isStored: boolean, help: string) {
    const value = form ? form[field] : "";
    let hint = help;
    if (isStored) hint = otherServer ? t("mail.sign_in.stored_other_server") : t("mail.sign_in.stored");
    return (
      <TextField
        size="small"
        fullWidth
        type="password"
        autoComplete="new-password"
        label={label}
        value={value}
        onChange={(event) => set({ [field]: event.target.value })}
        {...check(field, hint)}
        slotProps={{
          input: {
            endAdornment:
              isStored && !value ? (
                <InputAdornment position="end">
                  <LockIcon fontSize="small" color="action" titleAccess={t("mail.sign_in.stored_label")} />
                </InputAdornment>
              ) : undefined,
          },
          htmlInput: { "data-testid": `mail-${field}`, maxLength: MAX_LENGTH[field] },
        }}
      />
    );
  }

  const rootModeHint = !rootMode && form.auth !== "none" && (
    <Alert severity="info" variant="outlined" data-testid="mail-root-mode-hint">
      {t("mail.sign_in.root_mode_hint")}
    </Alert>
  );

  return (
    <Stack spacing={2} sx={{ maxWidth: 960, pb: 2 }} data-testid="mail-settings">
      <Typography variant="body2" color="text.secondary">
        {t("mail.intro")}{" "}
        <Link href={helpPage} target="_blank" rel="noopener" data-testid="mail-help">
          {t("mail.help")}
        </Link>
      </Typography>

      {loadError && (
        <Alert severity="warning" onClose={() => setLoadError(null)}>
          {loadError}
        </Alert>
      )}

      <MailStatePanel state={view.state} refreshing={refreshing} onRefresh={() => void refreshState()} />

      <Section title={t("mail.server.title")} intro={t("mail.server.intro")}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2} useFlexGap>
          <TextField
            size="small"
            fullWidth
            label={t("mail.server.host")}
            value={form.host}
            onChange={(event) => {
              setM365Replaced(null);
              set({ host: event.target.value });
            }}
            {...check("host", t("mail.server.host_help"))}
            slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.host } }}
          />
          <TextField
            size="small"
            label={t("mail.server.port")}
            value={form.port}
            onChange={(event) => set({ port: event.target.value })}
            {...check("port")}
            sx={{ width: { xs: "100%", md: 140 }, flexShrink: 0 }}
            slotProps={{ htmlInput: { inputMode: "numeric", dir: "ltr" } }}
          />
        </Stack>
        <TextField
          size="small"
          fullWidth
          select
          label={t("mail.server.security")}
          value={form.security}
          onChange={(event) => set(withSecurity(form, event.target.value as MailSecurity))}
          {...check("security", t("mail.server.security_help"))}
        >
          {SECURITY.map((security) => (
            <MenuItem key={security} value={security}>
              {t(`mail.server.security_${security}`)}
            </MenuItem>
          ))}
        </TextField>
      </Section>

      <Section title={t("mail.sign_in.title")}>
        <TextField
          size="small"
          fullWidth
          select
          label={t("mail.sign_in.auth")}
          value={form.auth}
          onChange={(event) => {
            const auth = event.target.value as MailAuth;
            setM365Replaced(replacedServer(form, auth));
            set(withAuth(form, auth));
          }}
          {...check("auth", t(`mail.sign_in.auth_${form.auth}_help`))}
        >
          {AUTH.map((auth) => (
            <MenuItem key={auth} value={auth}>
              {t(`mail.sign_in.auth_${auth}`)}
            </MenuItem>
          ))}
        </TextField>

        {form.auth === "password" && (
          <>
            <TextField
              size="small"
              fullWidth
              label={t("mail.sign_in.username")}
              value={form.username}
              onChange={(event) => set({ username: event.target.value })}
              {...check("username", t("mail.sign_in.username_help"))}
              slotProps={{
                htmlInput: { spellCheck: false, autoComplete: "off", dir: "ltr", maxLength: MAX_LENGTH.username },
              }}
            />
            {secretField("password", t("mail.sign_in.password"), stored.password_set, t("mail.sign_in.password_help"))}
            {rootModeHint}
          </>
        )}

        {form.auth === "microsoft365" && (
          <>
            {m365Replaced && (
              <Alert severity="info" variant="outlined" data-testid="mail-m365-server">
                {t("mail.sign_in.m365_server", { host: m365Replaced })}
              </Alert>
            )}
            <Link
              href={`${helpPage}#microsoft-365`}
              target="_blank"
              rel="noopener"
              variant="body2"
              data-testid="mail-m365-help"
            >
              {t("mail.sign_in.m365_help")}
            </Link>
            <TextField
              size="small"
              fullWidth
              type="email"
              label={t("mail.sign_in.mailbox")}
              value={form.username}
              onChange={(event) => set({ username: event.target.value })}
              {...check("username", t("mail.sign_in.mailbox_help"))}
              slotProps={{
                htmlInput: { spellCheck: false, autoComplete: "off", dir: "ltr", maxLength: MAX_LENGTH.username },
              }}
            />
            <TextField
              size="small"
              fullWidth
              label={t("mail.sign_in.tenant")}
              value={form.m365_tenant_id}
              onChange={(event) => set({ m365_tenant_id: event.target.value })}
              {...check("m365_tenant_id", t("mail.sign_in.tenant_help"))}
              slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.m365_tenant_id } }}
            />
            <TextField
              size="small"
              fullWidth
              label={t("mail.sign_in.client_id")}
              value={form.m365_client_id}
              onChange={(event) => set({ m365_client_id: event.target.value })}
              {...check("m365_client_id", t("mail.sign_in.client_id_help"))}
              slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.m365_client_id } }}
            />
            {secretField(
              "m365_client_secret",
              t("mail.sign_in.client_secret"),
              stored.m365_client_secret_set,
              t("mail.sign_in.client_secret_help"),
            )}
            {rootModeHint}
          </>
        )}
      </Section>

      <Section title={t("mail.certificates.title")} intro={t("mail.certificates.intro")}>
        <TextField
          size="small"
          fullWidth
          multiline
          minRows={3}
          maxRows={12}
          label={t("mail.certificates.pem")}
          value={form.ca_bundle}
          onChange={(event) => set({ ca_bundle: event.target.value })}
          {...check("ca_bundle", t("mail.certificates.pem_help"))}
          slotProps={{
            htmlInput: {
              spellCheck: false,
              dir: "ltr",
              maxLength: MAX_LENGTH.ca_bundle,
              style: { fontFamily: "monospace", fontSize: "0.8125rem" },
            },
          }}
        />
        {view.certificates === null ? (
          <Alert severity="error" data-testid="mail-certificates-unreadable">
            {t("mail.certificates.unreadable")}
          </Alert>
        ) : (
          view.certificates.length > 0 && (
            <Stack spacing={1} data-testid="mail-certificates">
              <Typography variant="caption" color="text.secondary">
                {t("mail.certificates.saved")}
              </Typography>
              {view.certificates.map((cert, index) => (
                <Box
                  key={`${cert.subject}-${index}`}
                  data-testid="mail-certificate"
                  sx={{
                    p: 1.5,
                    border: 1,
                    borderColor: cert.expired ? "error.main" : cert.ca_without_key_usage ? "warning.main" : "divider",
                    borderRadius: 1,
                  }}
                >
                  <Typography variant="body2" dir="ltr" sx={{ overflowWrap: "anywhere", textAlign: "start" }}>
                    {cert.subject}
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    display="block"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {t("mail.certificates.issuer", { issuer: cert.issuer })}
                  </Typography>
                  <Typography variant="caption" color={cert.expired ? "error" : "text.secondary"} display="block">
                    {cert.expired
                      ? t("mail.certificates.expired", { date: formatDateTime(cert.not_after) })
                      : t("mail.certificates.expires", { date: formatDateTime(cert.not_after) })}
                  </Typography>
                  {cert.ca_without_key_usage && (
                    <Typography
                      variant="caption"
                      color="warning"
                      display="block"
                      sx={{ mt: 0.5 }}
                      data-testid="mail-certificate-no-key-usage"
                    >
                      {t("mail.certificates.ca_without_key_usage")}
                    </Typography>
                  )}
                </Box>
              ))}
            </Stack>
          )
        )}
        {form.ca_bundle !== stored.ca_bundle && (
          <Typography variant="caption" color="text.secondary">
            {t("mail.certificates.unsaved")}
          </Typography>
        )}
        {/* What the mail service trusts is protected, as a secret is. */}
        {form.ca_bundle !== stored.ca_bundle && !rootMode && (
          <Alert severity="info" variant="outlined" data-testid="mail-certificates-root-mode-hint">
            {t("mail.certificates.root_mode_hint")}
          </Alert>
        )}
      </Section>

      <Section title={t("mail.sender.title")}>
        <TextField
          size="small"
          fullWidth
          type="email"
          label={t("mail.sender.from_email")}
          value={form.from_email}
          onChange={(event) => set({ from_email: event.target.value })}
          {...check("from_email", t("mail.sender.from_email_help"))}
          slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.from_email } }}
        />
        <TextField
          size="small"
          fullWidth
          label={t("mail.sender.from_name")}
          value={form.from_name}
          onChange={(event) => set({ from_name: event.target.value })}
          {...check("from_name", t("mail.sender.from_name_help"))}
          slotProps={{ htmlInput: { maxLength: MAX_LENGTH.from_name } }}
        />
        <TextField
          size="small"
          fullWidth
          label={t("mail.sender.helo_name")}
          value={form.helo_name}
          onChange={(event) => set({ helo_name: event.target.value })}
          {...check("helo_name", t("mail.sender.helo_name_help"))}
          slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.helo_name } }}
        />
      </Section>

      <Section title={t("mail.links.title")}>
        <TextField
          size="small"
          fullWidth
          type="url"
          label={t("mail.links.link_base")}
          value={form.link_base}
          onChange={(event) => set({ link_base: event.target.value })}
          {...check("link_base", t("mail.links.link_base_help"))}
          slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.link_base } }}
        />
      </Section>

      <Section title={t("mail.alerts.title")} intro={t("mail.alerts.intro")}>
        <RecipientsField
          value={form.admin_recipients}
          onChange={(admin_recipients) => set({ admin_recipients })}
          error={problemText("admin_recipients")}
        />
      </Section>

      <Section title={t("mail.test.title")} intro={t("mail.test.intro")}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} useFlexGap alignItems={{ xs: "stretch", sm: "flex-start" }}>
          <TextField
            size="small"
            fullWidth
            type="email"
            label={t("mail.test.to")}
            value={to}
            onChange={(event) => {
              setTo(event.target.value);
              setToTouched(true);
              setProblems((prev) => {
                if (!("to" in prev)) return prev;
                const next = { ...prev };
                delete next.to;
                return next;
              });
            }}
            {...check("to")}
            slotProps={{ htmlInput: { spellCheck: false, dir: "ltr", maxLength: MAX_LENGTH.to } }}
          />
          <Button
            variant="outlined"
            startIcon={testing ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
            disabled={testing}
            onClick={() => void sendTest()}
            sx={{ flexShrink: 0, minHeight: 40 }}
          >
            {testing ? t("mail.test.sending") : t("mail.test.send")}
          </Button>
        </Stack>
        {testOutcome && <TestOutcomeView outcome={testOutcome} />}
      </Section>

      {saveOutcome?.kind === "saved" && (
        <Alert severity="success" data-testid="mail-save-result" onClose={() => setSaveOutcome(null)}>
          {t("mail.save.saved")}
        </Alert>
      )}
      {saveOutcome?.kind === "problems" && (
        <Alert severity="error" data-testid="mail-save-result">
          {saveOutcome.rootMode ? t("mail.save.root_mode") : t("mail.save.problems")}
        </Alert>
      )}
      {saveOutcome?.kind === "error" && (
        <Alert severity="error" data-testid="mail-save-result">
          {saveOutcome.message}
        </Alert>
      )}
      <Stack direction="row" justifyContent="flex-end">
        <Button
          variant="contained"
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          disabled={saving}
          onClick={() => void save()}
          sx={{ width: { xs: "100%", sm: "auto" } }}
        >
          {saving ? t("common.saving") : t("common.save")}
        </Button>
      </Stack>
    </Stack>
  );
}
