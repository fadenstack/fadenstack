/**
 * Strips under the top bar of every admin page with what administrators should know now: whatever the
 * backend's notice sources say (``GET /api/admin/notices``; Enterprise: its licence's countdown, grace,
 * expiry and seats). Each is worded here, in the reader's language, from its code and values.
 *
 * A warning or a hint can be hidden until the browser is closed (remembered per notice, so a countdown
 * that moves on shows again); an error cannot.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import CloseIcon from "@mui/icons-material/Close";

import { adminNotices, type AdminNotice } from "~/api/admin";
import { fmtDay } from "~/lib/updateNotice";
import { useAsyncData } from "~/lib/useAsyncData";

const HIDDEN_KEY = "faden.notices.hidden";
/** The countdown moves once a day; an hour is soon enough to see it. */
const REFRESH_MS = 60 * 60 * 1000;

function noticeId(notice: AdminNotice): string {
  return `${notice.code}:${JSON.stringify(notice.params)}`;
}

function readHidden(): string[] {
  try {
    const stored = JSON.parse(sessionStorage.getItem(HIDDEN_KEY) ?? "[]");
    return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeHidden(ids: string[]): void {
  try {
    sessionStorage.setItem(HIDDEN_KEY, JSON.stringify(ids));
  } catch {
    // Storage blocked: the notice stays hidden until the page reloads.
  }
}

export function AdminNotices({ isSuperuser }: { isSuperuser: boolean }) {
  const { t, i18n } = useTranslation();
  const { data } = useAsyncData(
    () => (isSuperuser ? adminNotices.list().catch(() => [] as AdminNotice[]) : Promise.resolve([] as AdminNotice[])),
    [isSuperuser],
    { initialValue: [] as AdminNotice[], refreshMs: REFRESH_MS },
  );
  const [hidden, setHidden] = useState<string[]>(() => readHidden());

  const shown = data.filter((notice) => !hidden.includes(noticeId(notice)));
  if (!shown.length) return null;

  function text(notice: AdminNotice): string {
    const key = `notices.${notice.code}`;
    // A countdown's text has a form per number of days (``_one``, ``_other``), not one of its own.
    const count = typeof notice.params.days === "number" ? notice.params.days : undefined;
    if (!i18n.exists(key, { count })) return t("notices.unknown", { code: notice.code });
    const params = { ...notice.params };
    if (typeof params.date === "string") params.date = fmtDay(params.date, i18n.language);
    return t(key, { ...params, count });
  }

  function hide(notice: AdminNotice) {
    const next = [...hidden, noticeId(notice)];
    writeHidden(next);
    setHidden(next);
  }

  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, pt: { xs: 1.5, sm: 2 }, flexShrink: 0 }}>
      <Stack spacing={1}>
        {shown.map((notice) => (
          <Alert
            key={noticeId(notice)}
            severity={notice.severity}
            role={notice.severity === "error" ? "alert" : "status"}
            data-testid={`admin-notice-${notice.code}`}
            sx={{
              alignItems: "center",
              flexWrap: "wrap",
              "& .MuiAlert-message": { flex: "1 1 220px", minWidth: 0 },
              "& .MuiAlert-action": { pt: 0, alignItems: "center" },
            }}
            action={
              notice.severity !== "error" && (
                <Tooltip title={t("notices.hide")}>
                  <IconButton
                    color="inherit"
                    size="small"
                    aria-label={t("notices.hide")}
                    onClick={() => hide(notice)}
                    sx={{ width: 36, height: 36 }}
                  >
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )
            }
          >
            {text(notice)}
          </Alert>
        ))}
      </Stack>
    </Box>
  );
}
