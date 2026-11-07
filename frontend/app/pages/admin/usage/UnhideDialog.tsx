/**
 * Showing totals and a series about fewer than k people anyway: only a usage auditor with person-level
 * reporting on, and only for a stated reason of 10 to 500 characters, which the server records with the
 * view (M04, M07). Nothing is asked for before the reason is given.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { REASON_MAX, REASON_MIN, reasonOk } from "~/api/usage";

export default function UnhideDialog({
  open,
  k,
  onClose,
  onReason,
}: {
  open: boolean;
  k: number;
  onClose: () => void;
  onReason: (reason: string) => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();
  const bad = trimmed.length > 0 && !reasonOk(reason);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="usage-unhide-title">
      <DialogTitle id="usage-unhide-title">{t("usage.admin.unhide_title", { k })}</DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            {t("usage.admin.unhide_intro")}
          </Typography>
          <TextField
            label={t("usage.admin.people.reason_label")}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            multiline
            minRows={2}
            required
            error={bad}
            helperText={
              bad
                ? t("usage.errors.reason_required")
                : t("usage.admin.people.reason_help", { min: REASON_MIN, max: REASON_MAX, length: trimmed.length })
            }
            slotProps={{ htmlInput: { maxLength: REASON_MAX + 50 } }}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          variant="contained"
          disabled={!reasonOk(reason)}
          onClick={() => {
            onReason(trimmed);
            setReason("");
          }}
        >
          {t("usage.admin.people.submit")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
