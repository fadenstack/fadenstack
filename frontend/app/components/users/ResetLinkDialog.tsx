/**
 * A one-time link that sets a new password for one account, for an administrator to hand over: for a server
 * that sends no mail, or a person who cannot get it. Whoever holds the link gets the account, so it needs Root
 * Mode and a reason (the server records both), works once, for an hour or a day, and is shown only here.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import InputAdornment from "@mui/material/InputAdornment";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";

import { adminUsers, type AdminUser } from "~/api/admin";

/** The address the person opens: the token in the fragment, which never reaches a server or a log. */
export function resetLinkUrl(origin: string, token: string): string {
  return `${origin}/reset-password#${new URLSearchParams({ link: token }).toString()}`;
}

export function ResetLinkDialog({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const [reason, setReason] = useState("");
  const [validHours, setValidHours] = useState<1 | 24>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<{ url: string; expiresAt: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setReason("");
    setValidHours(1);
    setError(null);
    setMade(null);
    setCopied(false);
  }, [user]);

  if (!user) return null;
  const reasonOk = reason.trim().length >= 10;

  async function create() {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      const link = await adminUsers.createResetLink(user.id, reason.trim(), validHours);
      setMade({ url: resetLinkUrl(window.location.origin, link.token), expiresAt: link.expires_at });
    } catch (err: unknown) {
      setError(t("users.reset_link.failed", { error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!made) return;
    try {
      await navigator.clipboard.writeText(made.url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const until = made ? new Date(made.expiresAt).toLocaleString(i18n.language) : "";

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("users.reset_link.title", { email: user.email })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {made ? (
            <>
              <Alert severity="success" data-testid="reset-link-made">
                {t("users.reset_link.made", { email: user.email, until })}
              </Alert>
              <TextField
                label={t("users.reset_link.link")}
                value={made.url}
                InputProps={{
                  readOnly: true,
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton aria-label={t("users.reset_link.copy")} onClick={() => void copy()} edge="end">
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
                inputProps={{ "data-testid": "reset-link-url" }}
                onFocus={(e) => e.target.select()}
              />
              {copied && <Typography variant="body2" color="success.main">{t("users.reset_link.copied")}</Typography>}
              <Typography variant="body2" color="text.secondary">
                {t("users.reset_link.shown_once")}
              </Typography>
            </>
          ) : (
            <>
              <Typography variant="body2">{t("users.reset_link.body", { email: user.email })}</Typography>
              <TextField
                select
                label={t("users.reset_link.valid_for")}
                value={validHours}
                onChange={(e) => setValidHours(Number(e.target.value) === 24 ? 24 : 1)}
              >
                <MenuItem value={1}>{t("users.reset_link.one_hour")}</MenuItem>
                <MenuItem value={24}>{t("users.reset_link.one_day")}</MenuItem>
              </TextField>
              <TextField
                label={t("users.reset_link.reason")}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                helperText={t("users.reset_link.reason_help")}
                multiline
                minRows={2}
                required
              />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{made ? t("common.close") : t("common.cancel")}</Button>
        {!made && (
          <Button variant="contained" disabled={!reasonOk || busy} onClick={() => void create()}>
            {t("users.reset_link.create")}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
