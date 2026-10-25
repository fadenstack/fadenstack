/**
 * An account's owner confirms that a sign-in with an outside provider, which met their account, is theirs.
 *
 * The link in their mail opens the sign-in page with `#link=<token>`. Opening it changes nothing (mail scanners
 * open links too); the owner sees what would be linked and confirms, once.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { adminAuthProviders, SignInRefused, type LinkPreview } from "~/api/admin";

type Step = "loading" | "asking" | "linking" | "done" | "expired" | "refused" | "dismissed";

export function LinkConfirmation({ token }: { token: string }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("loading");
  const [preview, setPreview] = useState<LinkPreview | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminAuthProviders
      .linkPreview(token)
      .then((found) => {
        if (cancelled) return;
        setPreview(found);
        setStep("asking");
      })
      .catch(() => {
        if (!cancelled) setStep("expired");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function confirm() {
    setStep("linking");
    try {
      await adminAuthProviders.linkConfirm(token);
      setStep("done");
    } catch (err: unknown) {
      setStep(err instanceof SignInRefused && err.reason === "sign_in_expired" ? "expired" : "refused");
    }
  }

  if (step === "loading" || step === "dismissed") return null;
  if (step === "expired") return <Alert severity="warning" data-testid="link-expired">{t("auth.link_expired")}</Alert>;
  if (step === "refused") return <Alert severity="error" data-testid="link-refused">{t("auth.link_refused")}</Alert>;
  const provider = preview?.provider_name ?? "";
  if (step === "done") {
    return <Alert severity="success" data-testid="link-done">{t("auth.link_done", { provider })}</Alert>;
  }
  return (
    <Alert severity="info" icon={false} data-testid="link-confirmation">
      <Stack spacing={1.5}>
        <Typography variant="subtitle1" fontWeight={700}>
          {t("auth.link_title")}
        </Typography>
        <Typography variant="body2">
          {t("auth.link_body", {
            provider,
            identity: preview?.identity_email ?? provider,
            account: preview?.account_email ?? "",
          })}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button variant="contained" onClick={confirm} disabled={step === "linking"}>
            {t("auth.link_confirm")}
          </Button>
          <Button onClick={() => setStep("dismissed")} disabled={step === "linking"}>
            {t("auth.link_cancel")}
          </Button>
        </Stack>
      </Stack>
    </Alert>
  );
}
