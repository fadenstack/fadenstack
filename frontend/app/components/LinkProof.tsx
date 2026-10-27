/**
 * A sign-in with an outside provider met an existing account with the same address. It waits, in this browser
 * only, until the person proves the account is theirs: with its password, or with a code sent to its address
 * while the server can send mail. Then the sign-in is linked to the account, and the person is signed in.
 */
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { adminAuthProviders, SignInRefused, type LinkWaiting } from "~/api/admin";
import { refusalText } from "~/lib/signInRefusals";

type Mode = "password" | "code";

/** The words for why a proof or a code did not work; the sign-in refusals' own for the rest. */
const PROBLEMS: Record<string, string> = {
  wrong_credentials: "auth.link.wrong",
  sign_in_expired: "auth.link.expired",
  too_soon: "auth.link.too_soon",
  too_many: "auth.link.too_many",
  no_mail: "auth.link.no_mail",
};

export function LinkProof({ onSignedIn, onCancel }: { onSignedIn: () => void; onCancel: () => void }) {
  const { t } = useTranslation();
  const [waiting, setWaiting] = useState<LinkWaiting | null>(null);
  const [expired, setExpired] = useState(false);
  const [mode, setMode] = useState<Mode>("password");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminAuthProviders
      .linkWaiting()
      .then((found) => {
        if (cancelled) return;
        setWaiting(found);
        setCodeSent(found.code_sent);
        if (found.code_sent) setMode("code");
      })
      .catch(() => {
        if (!cancelled) setExpired(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function explain(err: unknown) {
    const reason = err instanceof SignInRefused ? err.reason : "";
    if (reason === "sign_in_expired") setExpired(true);
    setProblem(PROBLEMS[reason] ? t(PROBLEMS[reason]) : refusalText(t, reason));
  }

  async function sendCode() {
    setBusy(true);
    setProblem(null);
    try {
      await adminAuthProviders.linkSendCode();
      setCodeSent(true);
      setMode("code");
    } catch (err: unknown) {
      explain(err);
    } finally {
      setBusy(false);
    }
  }

  async function prove(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      if (mode === "password") await adminAuthProviders.linkWithPassword(password);
      else await adminAuthProviders.linkWithCode(code);
      onSignedIn();
    } catch (err: unknown) {
      explain(err);
      setBusy(false);
    }
  }

  if (expired) {
    return (
      <Stack spacing={2} data-testid="link-proof">
        <Alert severity="warning" data-testid="link-expired">
          {t("auth.link.expired")}
        </Alert>
        <Button onClick={onCancel}>{t("auth.link.back")}</Button>
      </Stack>
    );
  }
  if (!waiting) return null;
  const provider = waiting.provider_name;
  const account = waiting.account_email;

  return (
    <Stack spacing={2} component="form" onSubmit={prove} data-testid="link-proof">
      <Typography variant="subtitle1" fontWeight={700}>
        {t("auth.link.title", { provider })}
      </Typography>
      <Typography variant="body2">{t("auth.link.body", { provider, account })}</Typography>
      {problem && <Alert severity="error">{problem}</Alert>}

      {mode === "password" ? (
        <>
          <TextField
            label={t("auth.link.password_label", { account })}
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus
          />
          <Button type="submit" variant="contained" disabled={busy || !password}>
            {t("auth.link.submit")}
          </Button>
          {waiting.mail ? (
            <Button onClick={codeSent ? () => setMode("code") : sendCode} disabled={busy}>
              {codeSent ? t("auth.link.use_code") : t("auth.link.send_code")}
            </Button>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {t("auth.link.no_password_hint")}
            </Typography>
          )}
        </>
      ) : (
        <>
          {codeSent && (
            <Alert severity="info" data-testid="link-code-sent">
              {t("auth.link.code_sent", { account })}
            </Alert>
          )}
          <TextField
            label={t("auth.link.code_label")}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            inputProps={{ inputMode: "numeric" }}
            autoFocus
          />
          <Button type="submit" variant="contained" disabled={busy || !code.trim()}>
            {t("auth.link.submit")}
          </Button>
          <Stack direction="row" spacing={1} flexWrap="wrap">
            <Button onClick={sendCode} disabled={busy}>
              {t("auth.link.resend")}
            </Button>
            <Button onClick={() => setMode("password")} disabled={busy}>
              {t("auth.link.use_password")}
            </Button>
          </Stack>
        </>
      )}
      <Button onClick={onCancel} disabled={busy} size="small">
        {t("auth.link.cancel")}
      </Button>
    </Stack>
  );
}
