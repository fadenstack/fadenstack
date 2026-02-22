import { Navigate } from "react-router";

export default function AdminRouteRedirectPage() {
  return <Navigate to="/admin/dashboard" replace />;
}
