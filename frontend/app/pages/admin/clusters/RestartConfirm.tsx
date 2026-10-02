/**
 * The confirmation before a cluster setting that restarts the cluster or its
 * models: what will happen, Cancel, and the action -- busy while it runs.
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";

interface RestartConfirmProps {
  /** What restarts, and what that means for the models on it. */
  children: ReactNode;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function RestartConfirm({ children, confirmLabel, busy, onConfirm, onCancel }: RestartConfirmProps) {
  const { t } = useTranslation();
  return (
    <Alert
      severity="warning"
      sx={{ mt: 2 }}
      action={
        <Stack direction="row" spacing={1}>
          <Button size="small" color="inherit" onClick={onCancel} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button
            size="small"
            color="warning"
            variant="contained"
            onClick={onConfirm}
            disabled={busy}
            startIcon={busy ? <CircularProgress size={14} /> : undefined}
          >
            {confirmLabel}
          </Button>
        </Stack>
      }
    >
      {children}
    </Alert>
  );
}
