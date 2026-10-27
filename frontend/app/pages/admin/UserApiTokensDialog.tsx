/**
 * Someone's API tokens, for an administrator to revoke one: a script left
 * behind by someone who moved on, a token that leaked. It could be done only
 * through the API.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { adminUsers, type AdminUser, type ApiTokenInfo } from "~/api/admin";
import { TokenLastUse, TokenUsesDialog } from "~/components/users/ApiTokenUses";

export function UserApiTokensDialog({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language || undefined);
  const [tokens, setTokens] = useState<ApiTokenInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showing, setShowing] = useState<ApiTokenInfo | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    setError(null);
    try {
      setTokens(await adminUsers.listUserApiTokens(user.id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    }
  }, [user, t]);

  useEffect(() => {
    setTokens(null);
    void load();
  }, [load]);

  async function revoke(token: ApiTokenInfo) {
    if (!user) return;
    setBusy(token.id);
    try {
      await adminUsers.revokeUserApiToken(user.id, token.id);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={user !== null} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("users.tokens.title", { email: user?.email ?? "" })}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 0.5 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {tokens !== null && tokens.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              {t("users.tokens.none")}
            </Typography>
          )}
          {(tokens ?? []).map((tok) => {
            const expired = !tok.revoked_at && !!tok.expires_at && new Date(tok.expires_at).getTime() < Date.now();
            const live = !tok.revoked_at && !expired;
            return (
              <Box
                key={tok.id}
                data-user-token={tok.id}
                sx={{ display: "flex", alignItems: "center", gap: 1.5, opacity: live ? 1 : 0.6 }}
              >
                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography variant="body2" fontWeight={600} noWrap>
                    {tok.name || t("profile.unnamed_token")}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {t("profile.token_created", { date: formatDate(tok.created_at) })}
                    {" · "}
                    {tok.expires_at
                      ? t("profile.token_expires", { date: formatDate(tok.expires_at) })
                      : t("profile.token_never_expires")}
                  </Typography>
                  <TokenLastUse token={tok} />
                </Box>
                <Button size="small" onClick={() => setShowing(tok)}>
                  {t("api_tokens.show_uses")}
                </Button>
                {tok.revoked_at && <Chip size="small" label={t("profile.token_revoked")} />}
                {expired && <Chip size="small" label={t("profile.token_expired")} />}
                {live && (
                  <Button size="small" color="error" disabled={busy !== null} onClick={() => void revoke(tok)}>
                    {t("profile.revoke_token")}
                  </Button>
                )}
              </Box>
            );
          })}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.close")}</Button>
      </DialogActions>
      <TokenUsesDialog token={showing} load={adminUsers.listApiTokenUses} onClose={() => setShowing(null)} />
    </Dialog>
  );
}
