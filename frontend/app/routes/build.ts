/**
 * GET /_build -- which build of the console this server runs.
 *
 * Asked by open pages (lib/newBuild.ts) to notice an upgrade. Never cached:
 * the answer changes exactly when it matters.
 */
import { serverBuildVersion } from "~/lib/build.server";

export function loader() {
  return Response.json({ version: serverBuildVersion() }, { headers: { "Cache-Control": "no-store" } });
}
