/**
 * `/admin`: a superuser lands on the dashboard; someone a permission lets onto some console pages (the
 * Usage page for `usage:read`) on the first of them; anyone else on the chat; not signed in, on sign-in.
 */
import { useEffect, useState } from "react";
import { Navigate } from "react-router";

import { accessKeys, adminUsers } from "~/api/admin";
import { consolePages } from "~/lib/adminConstants";

export default function AdminRouteRedirectPage() {
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminUsers
      .meAccess()
      .then((access) => {
        if (cancelled) return;
        const pages = consolePages(access.is_superuser, accessKeys(access));
        setTarget(pages === null ? "/admin/dashboard" : (pages[0] ?? "/chat"));
      })
      .catch(() => {
        if (cancelled) return;
        setTarget("/login?next=%2Fadmin");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!target) return null;
  return <Navigate to={target} replace />;
}
