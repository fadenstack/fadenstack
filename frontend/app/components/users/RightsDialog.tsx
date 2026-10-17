/**
 * The administrator right and the auditor role of one account: grant, remove, or hand over to someone else.
 * Each needs Root Mode and a reason; the server records both names and the reason in the audit log.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { adminUsers, type AdminUser } from "~/api/admin";

type Right = "administrator" | "auditor";

export function RightsDialog({
  user,
  users,
  onClose,
  onChanged,
}: {
  user: AdminUser | null;
  users: AdminUser[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [right, setRight] = useState<Right>("administrator");
  const [toUserId, setToUserId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReason("");
    setToUserId("");
    setError(null);
    setRight(user?.is_superuser ? "administrator" : "auditor");
  }, [user]);

  if (!user) return null;
  const isAuditor = user.roles.some((role) => role.name === "auditor");
  const reasonOk = reason.trim().length >= 10;
  const others = users.filter((u) => u.id !== user.id && u.is_active);
  const holds = right === "administrator" ? user.is_superuser : isAuditor;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      onClose();
    } catch (err: unknown) {
      setError(t("users.rights.failed", { error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("users.rights.title", { email: user.email })}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <Typography variant="body2">
            {t("users.rights.administrator")}:{" "}
            {user.is_superuser ? t("users.rights.has") : t("users.rights.has_not")}
            {" · "}
            {t("users.rights.auditor")}: {isAuditor ? t("users.rights.has") : t("users.rights.has_not")}
          </Typography>
          <TextField
            label={t("users.rights.reason")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            helperText={t("users.rights.reason_help")}
            multiline
            minRows={2}
            required
          />
          <Stack direction="row" spacing={1}>
            {user.is_superuser ? (
              <Button
                variant="outlined"
                color="warning"
                disabled={!reasonOk || busy}
                onClick={() => void run(() => adminUsers.setAdministrator(user.id, false, reason.trim()))}
              >
                {t("users.rights.remove")}
              </Button>
            ) : (
              <Button
                variant="outlined"
                disabled={!reasonOk || busy || !user.is_active}
                onClick={() => void run(() => adminUsers.setAdministrator(user.id, true, reason.trim()))}
              >
                {t("users.rights.grant")}
              </Button>
            )}
          </Stack>
          <Divider />
          <Typography variant="subtitle2">{t("users.rights.hand_over")}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t("users.rights.hand_over_help")}
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <TextField
              select
              size="small"
              label={t("users.rights.right")}
              value={right}
              onChange={(e) => setRight(e.target.value as Right)}
              sx={{ minWidth: 180 }}
            >
              <MenuItem value="administrator">{t("users.rights.administrator")}</MenuItem>
              <MenuItem value="auditor">{t("users.rights.auditor")}</MenuItem>
            </TextField>
            <TextField
              select
              size="small"
              label={t("users.rights.to")}
              value={toUserId}
              onChange={(e) => setToUserId(e.target.value)}
              sx={{ flexGrow: 1 }}
            >
              {others.map((u) => (
                <MenuItem key={u.id} value={u.id}>
                  {u.email}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          variant="contained"
          disabled={!reasonOk || !toUserId || !holds || busy}
          onClick={() => void run(() => adminUsers.handOver(user.id, right, toUserId, reason.trim()))}
        >
          {t("users.rights.hand_over")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
