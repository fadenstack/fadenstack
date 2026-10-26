/**
 * The sign-in providers an account is linked to, and whether its password also signs it in; an unlink
 * button for each, off for the last way in. Shared by My Profile and the Users page.
 */
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { canUnlink, type LinkedSignIn, type SignIns } from "~/api/signIns";
import { formatDateTime } from "~/lib/dates";

export function providerLabel(link: LinkedSignIn): string {
  return link.provider_name ?? link.provider;
}

export function LinkedSignInsList({
  signIns,
  own,
  busy,
  onUnlink,
}: {
  signIns: SignIns;
  /** The signed-in person's own (My Profile), else someone else's (an administrator). */
  own: boolean;
  busy: boolean;
  onUnlink: (link: LinkedSignIn) => void;
}) {
  const { t } = useTranslation();
  const unlinkable = canUnlink(signIns);
  const lastWay = own ? t("sign_ins.last_way_own") : t("sign_ins.last_way_other");

  return (
    <Stack spacing={1.5} data-testid="linked-sign-ins">
      {signIns.links.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {t("sign_ins.none")}
        </Typography>
      )}
      {signIns.links.map((link) => (
        <Stack key={link.id} direction="row" spacing={2} alignItems="center" data-linked-sign-in={link.provider}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600}>
              {providerLabel(link)}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
              {[
                link.account_email,
                link.last_sign_in_at
                  ? t("sign_ins.last_sign_in", { when: formatDateTime(link.last_sign_in_at) })
                  : t("sign_ins.never_signed_in"),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Box>
          <Tooltip title={unlinkable ? "" : lastWay}>
            <span>
              <Button size="small" color="error" disabled={busy || !unlinkable} onClick={() => onUnlink(link)}>
                {t("sign_ins.unlink")}
              </Button>
            </span>
          </Tooltip>
        </Stack>
      ))}
      <Typography variant="body2" color="text.secondary">
        {signIns.password
          ? own ? t("sign_ins.password_own") : t("sign_ins.password_other")
          : own ? t("sign_ins.no_password_own") : t("sign_ins.no_password_other")}
      </Typography>
    </Stack>
  );
}
