/**
 * Set a new password: with the link from a reset mail (``#token=``; ``?token=`` in mails sent before), or with a
 * one-time link an administrator made (``#link=``). The token is in the address's fragment, which browsers never
 * send to a server; the page keeps it in memory and drops it from the address bar. Nobody is signed in: after
 * the new password is set, the person signs in as usual.
 */
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";

import { auth, ResetRefused } from "~/api/auth";
import { MIN_PASSWORD_LENGTH } from "~/lib/password";

type Source = { kind: "link" | "mail"; token: string } | null;

/** The token from the address, by where it came from; read once, then dropped from the address bar. */
function readToken(): Source {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const query = new URLSearchParams(window.location.search);
  const link = fragment.get("link")?.trim();
  const mailed = (fragment.get("token") ?? query.get("token"))?.trim();
  if (link || mailed) window.history.replaceState(null, "", window.location.pathname);
  if (link) return { kind: "link", token: link };
  if (mailed) return { kind: "mail", token: mailed };
  return null;
}

export default function ResetPasswordPage() {
  const { t } = useTranslation();
  // Read after the page is mounted: the server renders it without the fragment, which never reaches it.
  const [source, setSource] = useState<Source | undefined>(undefined);
  const [account, setAccount] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSource(readToken());
  }, []);

  useEffect(() => {
    if (source?.kind !== "link") return;
    let cancelled = false;
    auth
      .resetLinkPreview(source.token)
      .then((found) => {
        if (cancelled) return;
        if (found) setAccount(found.email);
        else setExpired(true);
      })
      .catch(() => {
        if (!cancelled) setExpired(true);
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!source) {
      setError(t("auth.reset_password_missing_token"));
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("auth.reset_password_too_short"));
      return;
    }
    if (password !== confirmPassword) {
      setError(t("auth.reset_password_mismatch"));
      return;
    }

    setLoading(true);
    setError(null);
    try {
      if (source.kind === "link") await auth.resetWithLink(source.token, password);
      else await auth.resetPassword(source.token, password);
      setDone(true);
    } catch (err: unknown) {
      if (err instanceof ResetRefused && err.reason === "expired") setExpired(true);
      else if (err instanceof ResetRefused && !err.reason.startsWith("status_")) setError(err.reason);
      else setError(err instanceof Error ? err.message : t("auth.reset_password_failed"));
    } finally {
      setLoading(false);
    }
  }

  const blocked = !source || expired || done || loading;

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: 2,
      }}
    >
      <Card sx={{ width: "100%", maxWidth: 460 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
            <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 0.5 }}>
              <BrandMark size={48} />
              <AppBrand size={24} />
            </Stack>
            <Typography variant="h5" fontWeight={700}>
              {t("auth.reset_password_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary" data-testid="reset-for">
              {account ? t("auth.reset_link.for_account", { email: account }) : t("auth.reset_password_desc")}
            </Typography>

            {source === null && <Alert severity="error">{t("auth.reset_password_missing_token")}</Alert>}
            {expired && (
              <Alert severity="warning" data-testid="reset-expired">
                {t("auth.reset_link.expired")}
              </Alert>
            )}
            {error && <Alert severity="error">{error}</Alert>}
            {done && <Alert severity="success">{t("auth.reset_password_success")}</Alert>}

            <TextField
              label={t("auth.password")}
              type="password"
              required
              value={password}
              disabled={blocked}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            <TextField
              label={t("auth.reset_password_confirm")}
              type="password"
              required
              value={confirmPassword}
              disabled={blocked}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />

            <Button type="submit" variant="contained" size="large" disabled={blocked}>
              {t("auth.reset_password_submit")}
            </Button>
            <Button component={RouterLink} to="/login" size="small" variant={done ? "contained" : "text"}>
              {t("auth.back_login")}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
