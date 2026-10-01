/**
 * A user's developer mode: agents may attach any local MCP server on that
 * user's machines. It never widens what the server allows, reaches the user's
 * agents at their next token refresh, and can end by itself.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { adminUsers, hasDeveloperMode, type AdminUser } from "~/api/admin";
import { FormDialog } from "~/components/FormDialog";
import { useAction } from "~/lib/useAction";

/** An ISO time as a `datetime-local` value, in local time. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface DeveloperModeDialogProps {
  user: AdminUser | null;
  onClose: () => void;
  onSaved: () => void;
}

export function DeveloperModeDialog({ user, onClose, onSaved }: DeveloperModeDialogProps) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [until, setUntil] = useState("");
  const save = useAction();

  useEffect(() => {
    if (!user) return;
    setEnabled(hasDeveloperMode(user));
    setUntil(hasDeveloperMode(user) ? toLocalInput(user.developer_mode_until) : "");
    save.setError(null);
  }, [user, save.setError]);

  const untilInPast = enabled && until !== "" && new Date(until).getTime() <= Date.now();

  return (
    <FormDialog
      open={user !== null}
      title={t("users.developer_mode.title")}
      loading={save.busy}
      submitLabel={t("common.save")}
      cancelLabel={t("common.cancel")}
      submitDisabled={untilInPast}
      onClose={onClose}
      onSubmit={() =>
        void save.run(
          () => adminUsers.setDeveloperMode(user!.id, enabled, enabled && until ? new Date(until).toISOString() : null),
          onSaved,
        )
      }
      maxWidth="xs"
    >
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          {user?.email}
        </Typography>
        <Alert severity="info" variant="outlined">
          {t("users.developer_mode.explain")}
        </Alert>
        {save.error && <Alert severity="error">{save.error}</Alert>}
        <FormControlLabel
          control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
          label={t("users.developer_mode.enabled")}
        />
        <TextField
          type="datetime-local"
          label={t("users.developer_mode.until")}
          value={until}
          disabled={!enabled}
          error={untilInPast}
          helperText={untilInPast ? t("users.developer_mode.until_past") : t("users.developer_mode.until_help")}
          onChange={(e) => setUntil(e.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
      </Stack>
    </FormDialog>
  );
}
