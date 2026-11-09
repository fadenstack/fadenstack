/**
 * The second sign-in before Bypass is switched on in a chat app. The app opens ``/step-up#code=XXXX-XXXX`` in the
 * system browser; the code is in the address's fragment, which browsers never send to a server. The page posts it
 * once, drops it from the address bar and goes on with the browser's own cookie. Nobody needs to be signed in to
 * the console: an open console session counts for nothing here.
 */
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";

import { StepUpRefused, stepUpApi, type StepUpProvider, type StepUpState } from "~/api/stepUp";
import { formatDateTime } from "~/lib/dates";

/** Refusals the sign-in providers' return trip may name in ``?refused=``; anything else reads as the generic one. */
const REFUSALS = ["not_fresh", "wrong_account", "expired", "provider_error", "provider_unavailable"];
/** Errors of the page's own requests with a text of their own. */
const ERRORS = [
  "wrong_credentials",
  "use_sign_in_provider",
  "sign_in_too_old",
  "sign_in_first",
  "opened_elsewhere",
  "expired",
  "provider_unavailable",
  "not_offered",
];

const ENDED: StepUpState = {
  state: "expired", password: false, providers: [], agent: null, device: null, longest_minutes: null,
  signed_in_as: null, method: null, until: null,
};

/** The link's code from the address, then dropped from it (the path stays). */
function takeCode(): string | null {
  const code = new URLSearchParams(window.location.hash.slice(1)).get("code")?.trim();
  if (window.location.hash) window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return code || null;
}

/** The refusal a provider's return trip named, then dropped from the address. */
function takeRefused(): string | null {
  const query = new URLSearchParams(window.location.search);
  const refused = query.get("refused");
  if (refused === null) return null;
  window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  return refused;
}

/** A directory's own form: the account's name and password again. */
function DirectoryForm({
  provider, busy, onSubmit,
}: {
  provider: StepUpProvider;
  busy: boolean;
  onSubmit: (key: string, username: string, password: string) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSubmit(provider.key, username, password);
  }

  return (
    <Stack spacing={1.5} component="form" onSubmit={handleSubmit} aria-label={provider.name}>
      <Typography variant="subtitle2">{provider.name}</Typography>
      <TextField
        label={t("auth.account_name")}
        required
        value={username}
        disabled={busy}
        onChange={(e) => setUsername(e.target.value)}
        autoComplete="username"
      />
      <TextField
        label={t("auth.password")}
        type="password"
        required
        value={password}
        disabled={busy}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
      />
      <Button type="submit" variant="outlined" disabled={busy}>
        {t("auth.step_up.sign_in_again")}
      </Button>
    </Stack>
  );
}

export default function StepUpPage() {
  const { t } = useTranslation();
  const [view, setView] = useState<StepUpState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const started = useRef(false);

  /** A refusal in words: only the allow-listed texts, never the server's. */
  function wording(err: unknown): string {
    if (err instanceof StepUpRefused) {
      if (err.code === "too_many_tries") return t("auth.step_up.error.too_many_tries", { seconds: err.retryAfter ?? 60 });
      if (ERRORS.includes(err.code)) return t(`auth.step_up.error.${err.code}`);
    }
    return t("auth.step_up.error.other");
  }

  /** What follows a failed request: some refusals also change what the page shows. */
  async function failed(err: unknown) {
    if (err instanceof StepUpRefused && err.code === "denied") {
      setError(null);
      setView({ ...ENDED, state: "denied" });
      return;
    }
    if (err instanceof StepUpRefused && err.code === "expired") {
      setError(null);
      setView(ENDED);
      return;
    }
    setError(wording(err));
    if (err instanceof StepUpRefused && err.code === "sign_in_too_old") {
      try {
        setView(await stepUpApi.state());
      } catch {
        // The words above stand.
      }
    }
  }

  async function run(call: () => Promise<StepUpState>) {
    setBusy(true);
    setError(null);
    try {
      setView(await call());
    } catch (err: unknown) {
      await failed(err);
    } finally {
      setBusy(false);
    }
  }

  // Read after the page is mounted: the server renders it without the fragment, which never reaches it.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = takeCode();
    const refused = takeRefused();
    if (refused !== null) {
      setError(REFUSALS.includes(refused) ? t(`auth.step_up.refused.${refused}`) : t("auth.step_up.refused.other"));
    }
    setBusy(true);
    (code ? stepUpApi.begin(code) : stepUpApi.state())
      .then(setView)
      .catch(async (err: unknown) => {
        if (refused === null || code) await failed(err);
        else setView(ENDED);
      })
      .finally(() => setBusy(false));
    // Once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handlePassword(event: FormEvent) {
    event.preventDefault();
    void run(() => stepUpApi.password(email, password));
  }

  async function handleSso(key: string) {
    setBusy(true);
    setError(null);
    try {
      const target = new URL(await stepUpApi.sso(key), window.location.href);
      if (target.protocol !== "https:" && target.protocol !== "http:") throw new Error("not a web address");
      window.location.assign(target.href);
    } catch (err: unknown) {
      await failed(err);
      setBusy(false);
    }
  }

  const redirects = view?.providers.filter((p) => p.kind === "redirect") ?? [];
  const directories = view?.providers.filter((p) => p.kind === "password") ?? [];

  return (
    <Box sx={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", p: 2 }}>
      <Card sx={{ width: "100%", maxWidth: 460 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={2.5}>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 0.5 }}>
              <BrandMark size={48} />
              <AppBrand size={24} />
            </Stack>
            <Typography variant="h5" fontWeight={700}>
              {t("auth.step_up.title")}
            </Typography>

            {error && (
              <Alert severity="error" data-testid="step-up-error">
                {error}
              </Alert>
            )}

            {view === null && !busy && !error && <Alert severity="warning">{t("auth.step_up.expired")}</Alert>}

            {view?.state === "sign_in" && (
              <>
                <Typography variant="body2" color="text.secondary">
                  {t("auth.step_up.intro")}
                </Typography>
                {view.password && (
                  <Stack spacing={1.5} component="form" onSubmit={handlePassword} aria-label={t("auth.local_account")}>
                    <TextField
                      label={t("auth.email")}
                      type="email"
                      required
                      value={email}
                      disabled={busy}
                      onChange={(e) => setEmail(e.target.value)}
                      autoComplete="username"
                    />
                    <TextField
                      label={t("auth.password")}
                      type="password"
                      required
                      value={password}
                      disabled={busy}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="current-password"
                    />
                    <Button type="submit" variant="contained" size="large" disabled={busy}>
                      {t("auth.step_up.sign_in_again")}
                    </Button>
                  </Stack>
                )}
                {directories.map((provider) => (
                  <Box key={provider.key}>
                    {view.password && <Divider sx={{ mb: 2 }} />}
                    <DirectoryForm
                      provider={provider}
                      busy={busy}
                      onSubmit={(key, name, secret) => void run(() => stepUpApi.directory(key, name, secret))}
                    />
                  </Box>
                ))}
                {redirects.length > 0 && (
                  <Stack spacing={1}>
                    {redirects.map((provider) => (
                      <Button
                        key={provider.key}
                        type="button"
                        variant="outlined"
                        disabled={busy}
                        onClick={() => void handleSso(provider.key)}
                      >
                        {t("auth.sign_in_with", { provider: provider.name })}
                      </Button>
                    ))}
                  </Stack>
                )}
              </>
            )}

            {view?.state === "decide" && (
              <>
                <Stack spacing={0.5} data-testid="step-up-decide">
                  {view.agent && (
                    <Typography variant="body2">
                      <strong>{t("auth.step_up.agent")}</strong> {view.agent}
                    </Typography>
                  )}
                  {view.device && (
                    <Typography variant="body2">
                      <strong>{t("auth.step_up.device")}</strong> {view.device}
                    </Typography>
                  )}
                </Stack>
                <Typography variant="body2">{t("auth.step_up.what")}</Typography>
                {view.longest_minutes !== null && (
                  <Typography variant="body2">{t("auth.step_up.longest", { minutes: view.longest_minutes })}</Typography>
                )}
                {view.signed_in_as && (
                  <Typography variant="body2" color="text.secondary">
                    {t("auth.step_up.signed_in_as", { account: view.signed_in_as })}
                  </Typography>
                )}
                {view.until && (
                  <Typography variant="body2" color="text.secondary">
                    {t("auth.step_up.decide_before", { time: formatDateTime(view.until) })}
                  </Typography>
                )}
                <Stack direction="row" spacing={1.5}>
                  <Button type="button" variant="outlined" color="error" disabled={busy} onClick={() => void run(stepUpApi.deny)}>
                    {t("auth.step_up.deny")}
                  </Button>
                  <Button type="button" variant="contained" disabled={busy} onClick={() => void run(stepUpApi.approve)}>
                    {t("auth.step_up.approve")}
                  </Button>
                </Stack>
              </>
            )}

            {view?.state === "approved" && <Alert severity="success">{t("auth.step_up.approved")}</Alert>}
            {view?.state === "denied" && <Alert severity="info">{t("auth.step_up.denied")}</Alert>}
            {view?.state === "expired" && <Alert severity="warning">{t("auth.step_up.expired")}</Alert>}
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
