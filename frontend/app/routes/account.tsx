/**
 * Account layout: pages that belong to the signed-in person rather than to a module (their own usage).
 * Signed in or sent to sign in; nothing else is asked -- unlike the chat layout, it opens whether or not the
 * chat module is on, so everyone can see their own usage (M03) also where only agents or the API are used.
 */
import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";

import { accessKeys, adminUsers } from "~/api/admin";
import { auth, type AuthUser } from "~/api/auth";
import { applyUserLanguage } from "~/lib/userLanguage";

export default function AccountLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await auth.me();
        if (cancelled) return;
        void applyUserLanguage(["common"]);
        // What the person may do (for links to other pages); best-effort, the server decides anyway.
        try {
          const access = await adminUsers.meAccess();
          if (!cancelled) setPermissions(new Set(access.is_superuser ? ["*"] : accessKeys(access)));
        } catch {
          // none known: no links that need a permission
        }
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`, { replace: true });
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);

  if (!user) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh" }}>
        <CircularProgress />
      </Box>
    );
  }
  return <Outlet context={{ user, permissions }} />;
}
