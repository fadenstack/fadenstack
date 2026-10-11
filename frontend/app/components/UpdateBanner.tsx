/**
 * A strip under the top bar of every admin page when this server should be
 * upgraded: a critical or security release is out, or the release it runs is
 * past its support.
 *
 * A security update can be hidden until the next release (remembered in this
 * browser); a critical one, and a release past its support, cannot. Not shown
 * on the Updates page, which says all of it already.
 */
import { useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import CloseIcon from "@mui/icons-material/Close";

import {
  fmtDay,
  readDismissedVersion,
  updateBannerState,
  useUpdateNotice,
  writeDismissedVersion,
} from "~/lib/updateNotice";

export const UPDATES_PATH = "/admin/updates";

export function UpdateBanner({ isSuperuser, pathname }: { isSuperuser: boolean; pathname: string }) {
  const { t, i18n } = useTranslation();
  const notice = useUpdateNotice(isSuperuser);
  const [dismissed, setDismissed] = useState<string | null>(() => readDismissedVersion());

  if (!isSuperuser || pathname.startsWith(UPDATES_PATH)) return null;
  const data = notice.data;
  const state = updateBannerState(data, dismissed);
  if (!data || !state) return null;

  const until = data.supported_until ? fmtDay(data.supported_until, i18n.language) : "";
  const unsupportedText = until
    ? t("updates.banner.unsupported", { current: data.current, until })
    : t("updates.banner.unsupported_plain", { current: data.current });
  const parts: string[] = [];
  if (state.kind === "critical" || state.kind === "security") {
    parts.push(t(`updates.banner.${state.kind}`, { version: data.latest ?? "", current: data.current }));
  }
  if (state.unsupported) parts.push(unsupportedText);

  function dismiss() {
    if (!data?.latest) return;
    writeDismissedVersion(data.latest);
    setDismissed(data.latest);
  }

  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, pt: { xs: 1.5, sm: 2 }, flexShrink: 0 }}>
      <Alert
        severity={state.kind === "critical" ? "error" : "warning"}
        role="status"
        data-testid="update-banner"
        sx={{
          alignItems: "center",
          // On a phone the link and the close button go under the text
          // rather than squeezing it into a narrow column.
          flexWrap: "wrap",
          "& .MuiAlert-message": { flex: "1 1 220px", minWidth: 0 },
          "& .MuiAlert-action": { pt: 0, alignItems: "center" },
        }}
        action={
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Button
              component={RouterLink}
              to={UPDATES_PATH}
              color="inherit"
              size="small"
              sx={{ minHeight: 36, whiteSpace: "nowrap" }}
            >
              {t("updates.banner.open")}
            </Button>
            {state.dismissible && (
              <Tooltip title={t("updates.banner.dismiss")}>
                <IconButton
                  color="inherit"
                  size="small"
                  aria-label={t("updates.banner.dismiss")}
                  onClick={dismiss}
                  sx={{ width: 36, height: 36 }}
                >
                  <CloseIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        }
      >
        {parts.join(" ")}
      </Alert>
    </Box>
  );
}
