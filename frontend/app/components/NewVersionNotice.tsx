/**
 * After an upgrade, open pages move to the new console by themselves.
 *
 * Every couple of minutes, and whenever the tab comes back, the page asks
 * which build the server runs (lib/newBuild.ts). A newer one is loaded:
 *
 * - at once, while the tab is in the background and nothing is typed in it;
 * - at the next navigation, which leaves the screen anyway;
 * - or with the notice's Reload, which says why.
 *
 * Never while text is typed and unsent -- nothing keeps drafts, and a chat
 * message half written would be lost.
 */
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import Button from "@mui/material/Button";
import Snackbar from "@mui/material/Snackbar";

import { hasTypedText, serverRunsNewerBuild } from "~/lib/newBuild";

const CHECK_MS = 120_000;

export default function NewVersionNotice({
  checkMs = CHECK_MS,
  reload = () => window.location.reload(),
}: {
  checkMs?: number;
  reload?: () => void;
}) {
  const { t } = useTranslation();
  const [available, setAvailable] = useState(false);
  const location = useLocation();
  const lastPath = useRef(location.pathname);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  const reloadIfUnseen = () => {
    if (document.visibilityState === "hidden" && !hasTypedText()) reloadRef.current();
  };

  // Ask, until the answer is "yes".
  useEffect(() => {
    if (available) return;
    let cancelled = false;
    const check = async () => {
      if (!(await serverRunsNewerBuild()) || cancelled) return;
      setAvailable(true);
      reloadIfUnseen();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    const timer = window.setInterval(() => void check(), checkMs);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available, checkMs]);

  // Known to be old: going to another screen loads the new console instead --
  // unless the navigation carries something (a new chat's first message rides
  // in its state, and is sent by the page it lands on).
  useEffect(() => {
    if (available && location.pathname !== lastPath.current && location.state == null) reloadRef.current();
    lastPath.current = location.pathname;
  }, [available, location.pathname, location.state]);

  // And so does leaving the tab, once nothing typed is left in it.
  useEffect(() => {
    if (!available) return;
    document.addEventListener("visibilitychange", reloadIfUnseen);
    return () => document.removeEventListener("visibilitychange", reloadIfUnseen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available]);

  return (
    <Snackbar
      open={available}
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      message={t("app.new_version")}
      data-testid="new-version"
      action={
        <Button color="inherit" size="small" onClick={() => reloadRef.current()} sx={{ minHeight: 36 }}>
          {t("app.reload")}
        </Button>
      }
    />
  );
}
