/**
 * The build this server runs, as the browser can compare it.
 *
 * React Router gives every build a manifest version; a page carries its own
 * as `window.__reactRouterManifest.version`. A page loaded before an upgrade
 * keeps it -- with route discovery "initial" it never asks the server again
 * -- so comparing the two tells it a newer console is there (lib/newBuild.ts).
 */
import * as build from "virtual:react-router/server-build";

export function serverBuildVersion(): string {
  return build.assets.version;
}
