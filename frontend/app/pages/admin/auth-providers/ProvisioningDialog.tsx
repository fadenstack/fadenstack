/**
 * Provisioning for one sign-in provider (OIDC, SAML): its identity provider adds, changes and suspends people
 * here, and keeps teams, over SCIM 2.0. Switching it on makes the connection's token; it is shown once, then
 * only its last four characters. Replacing it stops the old one at once; switching off keeps what it made.
 *
 * Mounted only while open, so a token shown is gone with the dialog.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { scimConnectionsApi, type ScimConnection, type ScimToken } from "~/api/scimConnections";
import type { SignInProvider } from "~/api/signInProviders";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { formatDateTime } from "~/lib/dates";
import { helpUrl } from "~/lib/pageHelp";
import { useAction } from "~/lib/useAction";

import { Address } from "./Address";

type Confirming = "replace" | "off" | null;

interface Made {
  token: ScimToken;
  /** It replaced a token the provider still has. */
  replaced: boolean;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}

export function ProvisioningDialog({
  provider,
  connection,
  onClose,
  onChanged,
}: {
  provider: SignInProvider;
  /** Its connection; ``null`` while provisioning is off. */
  connection: ScimConnection | null;
  onClose: () => void;
  /** The connection was made, replaced or ended: load the connections again. */
  onChanged: () => unknown;
}) {
  const { t, i18n } = useTranslation();
  const action = useAction();
  const [made, setMade] = useState<Made | null>(null);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const plainHttp = typeof window !== "undefined" && window.location.protocol !== "https:";

  const { name, key } = provider;

  async function makeToken() {
    const replaced = connection !== null;
    setConfirming(null);
    await action.run(() => scimConnectionsApi.makeToken(key), async (token) => {
      setMade({ token, replaced });
      await onChanged();
    });
  }

  async function switchOff() {
    const ended = await action.run(() => scimConnectionsApi.remove(key), onChanged);
    setConfirming(null);
    if (ended) setMade(null);
  }

  const baseUrl = made?.token.base_url ?? connection?.base_url;

  return (
    <>
      <Dialog open onClose={action.busy ? undefined : onClose} maxWidth="sm" fullWidth>
        <DialogTitle>{t("auth_providers.provisioning.title", { name })}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }} data-testid="provisioning-dialog">
            <Typography variant="body2" color="text.secondary">
              {t("auth_providers.provisioning.intro")}{" "}
              <Link href={helpUrl("administer/sign-in-provisioning", i18n.language)} target="_blank" rel="noopener">
                {t("auth_providers.provisioning.help")}
              </Link>
            </Typography>
            {plainHttp && <Alert severity="warning">{t("auth_providers.provisioning.needs_https")}</Alert>}
            {made && (
              <Alert severity="success" data-testid="provisioning-token">
                <Stack spacing={1.5}>
                  <span>
                    {t(made.replaced ? "auth_providers.provisioning.token_replaced" : "auth_providers.provisioning.token_made")}
                  </span>
                  <Address label={t("auth_providers.provisioning.token")} value={made.token.token} />
                </Stack>
              </Alert>
            )}
            {baseUrl ? (
              <Address
                label={t("auth_providers.provisioning.base_url")}
                value={baseUrl}
                helperText={t("auth_providers.provisioning.base_url_help")}
              />
            ) : (
              <Typography variant="body2">{t("auth_providers.provisioning.is_off")}</Typography>
            )}
            {connection && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={3} data-testid="provisioning-state">
                <Fact
                  label={t("auth_providers.provisioning.token")}
                  value={t("auth_providers.provisioning.token_hint", {
                    hint: connection.token_hint,
                    when: formatDateTime(connection.token_made_at),
                  })}
                />
                <Fact
                  label={t("auth_providers.provisioning.last_push")}
                  value={formatDateTime(connection.last_push_at, t("auth_providers.provisioning.never"))}
                />
                <Fact
                  label={t("auth_providers.provisioning.last_error")}
                  value={connection.last_error
                    ? t("auth_providers.provisioning.error_at", {
                        error: connection.last_error,
                        when: formatDateTime(connection.last_error_at),
                      })
                    : t("auth_providers.provisioning.no_error")}
                />
              </Stack>
            )}
            {action.error && <Alert severity="error">{action.error}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          {connection ? (
            <>
              <Button color="error" disabled={action.busy} onClick={() => setConfirming("off")}>
                {t("auth_providers.provisioning.switch_off")}
              </Button>
              <Button variant="outlined" disabled={action.busy} onClick={() => setConfirming("replace")}>
                {t("auth_providers.provisioning.replace")}
              </Button>
            </>
          ) : (
            <Button variant="contained" disabled={action.busy} onClick={makeToken}>
              {t("auth_providers.provisioning.switch_on")}
            </Button>
          )}
          <Button onClick={onClose} disabled={action.busy}>
            {t("common.close")}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={confirming === "replace"}
        title={t("auth_providers.provisioning.replace")}
        message={t("auth_providers.provisioning.replace_confirm", { name })}
        confirmLabel={t("auth_providers.provisioning.replace")}
        confirmColor="warning"
        cancelLabel={t("common.cancel")}
        loading={action.busy}
        onConfirm={makeToken}
        onClose={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming === "off"}
        title={t("auth_providers.provisioning.switch_off")}
        message={t("auth_providers.provisioning.switch_off_confirm", { name })}
        confirmLabel={t("auth_providers.provisioning.switch_off")}
        cancelLabel={t("common.cancel")}
        loading={action.busy}
        onConfirm={switchOff}
        onClose={() => setConfirming(null)}
      />
    </>
  );
}
