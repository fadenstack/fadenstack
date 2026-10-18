import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function ProvenanceGuardLayout() {
  return (
    <ModuleGuard module="provenance">
      <Outlet />
    </ModuleGuard>
  );
}
