/**
 * What is known of an API token's use: every request the gateway let in with it is recorded (when, from
 * which address, with which client, on which route), so people and administrators see who uses a token
 * and how. Shown on My Profile, in someone's tokens and on Access Control → API tokens.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";

import type { ApiTokenInfo, ApiTokenUse } from "~/api/admin";

function useWhen() {
  const { i18n } = useTranslation();
  return (iso: string) =>
    new Date(iso).toLocaleString(i18n.language || undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** The token's last use and how often it was used lately, in one line. */
export function TokenLastUse({ token }: { token: ApiTokenInfo }) {
  const { t } = useTranslation();
  const when = useWhen();
  if (!token.last_used_at) {
    return (
      <Typography variant="caption" color="text.secondary" component="div" data-token-last-use="never">
        {t("api_tokens.never_used")}
      </Typography>
    );
  }
  return (
    <Typography variant="caption" color="text.secondary" component="div" data-token-last-use={token.last_used_at}>
      {t("api_tokens.last_use", {
        when: when(token.last_used_at),
        address: token.last_used_address || t("api_tokens.unknown_address"),
      })}
      {token.last_used_client ? ` · ${token.last_used_client}` : ""}
      {" · "}
      {t("api_tokens.recent_uses", { count: token.recent_uses ?? 0 })}
    </Typography>
  );
}

/** A token's latest uses, newest first. */
export function TokenUsesDialog({
  token,
  load,
  onClose,
}: {
  token: ApiTokenInfo | null;
  load: (tokenId: string) => Promise<ApiTokenUse[]>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const when = useWhen();
  const [uses, setUses] = useState<ApiTokenUse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUses(null);
    setError(null);
    if (!token) return;
    load(token.id).then(setUses, (err: unknown) =>
      setError(err instanceof Error ? err.message : t("common.error_unexpected")),
    );
  }, [token, load, t]);

  return (
    <Dialog open={token !== null} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("api_tokens.uses_title", { name: token?.name || t("profile.unnamed_token") })}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error">{error}</Alert>}
        {uses !== null && uses.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            {t("api_tokens.uses_none")}
          </Typography>
        )}
        {uses !== null && uses.length > 0 && (
          <Table size="small" data-token-uses={token?.id}>
            <TableHead>
              <TableRow>
                <TableCell>{t("api_tokens.col_when")}</TableCell>
                <TableCell>{t("api_tokens.col_address")}</TableCell>
                <TableCell>{t("api_tokens.col_client")}</TableCell>
                <TableCell>{t("api_tokens.col_route")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {uses.map((use, index) => (
                <TableRow key={`${use.at}-${index}`}>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{when(use.at)}</TableCell>
                  <TableCell>{use.address ?? "—"}</TableCell>
                  <TableCell sx={{ wordBreak: "break-word" }}>{use.client ?? "—"}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}>{use.route ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1.5 }}>
          {t("api_tokens.uses_kept")}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.close")}</Button>
      </DialogActions>
    </Dialog>
  );
}
