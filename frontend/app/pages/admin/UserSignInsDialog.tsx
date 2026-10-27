/**
 * Someone's linked sign-ins, for an administrator: a provider linked by mistake, or an account at a provider
 * the person no longer uses. Unlinking is audited, and never takes their last way in: a password reset link
 * gives them a password first.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";

import type { AdminUser } from "~/api/admin";
import { signInsApi, type LinkedSignIn, type SignIns } from "~/api/signIns";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { LinkedSignInsList, providerLabel } from "~/components/users/LinkedSignInsList";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

/** Mounted only while open: the key is the person, so another person's start from nothing. */
export function UserSignInsDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const { t } = useTranslation();
  const { data, error, refresh } = useAsyncData<SignIns | null>(() => signInsApi.of(user.id), [user.id], {
    initialValue: null,
  });
  const action = useAction();
  const [unlinking, setUnlinking] = useState<LinkedSignIn | null>(null);

  async function unlink() {
    if (!unlinking) return;
    await action.run(() => signInsApi.unlink(user.id, unlinking.id), () => refresh());
    setUnlinking(null);
  }

  const name = unlinking ? providerLabel(unlinking) : "";
  return (
    <>
      <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
        <DialogTitle>{t("sign_ins.of_title", { email: user.email })}</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error">{error}</Alert>}
          {action.error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {action.error === "last_way" ? t("sign_ins.last_way_other") : action.error}
            </Alert>
          )}
          {data ? (
            <LinkedSignInsList signIns={data} own={false} busy={action.busy} onUnlink={setUnlinking} />
          ) : (
            !error && <Typography variant="body2">{t("common.loading")}</Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{t("common.close")}</Button>
        </DialogActions>
      </Dialog>
      <ConfirmDialog
        open={!!unlinking}
        title={t("sign_ins.unlink_title", { name })}
        message={t("sign_ins.unlink_confirm_other", { name, email: user.email })}
        confirmLabel={t("sign_ins.unlink")}
        cancelLabel={t("common.cancel")}
        loading={action.busy}
        onConfirm={() => void unlink()}
        onClose={() => setUnlinking(null)}
      />
    </>
  );
}
