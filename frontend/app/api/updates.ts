/**
 * Updates (`/api/admin/updates`, administrators only): which Fadenstack release
 * this server runs, what is newer, and the commands that upgrade it. Nothing
 * here installs anything -- an operator runs the commands on the server.
 *
 * The release list is read from fadenstack.com once a day (Settings > Updates),
 * on "Check now", or uploaded by hand on a server without internet.
 */
import { json, requester } from "./http";

const request = requester("/api/admin/updates");

/** How urgent the newer releases are, the most urgent of them. */
export type UpdateSeverity = "none" | "normal" | "security" | "critical";
/** current | update (a newer one is here to take) | mismatch (another release) | unknown | stopped */
export type PartStatus = "current" | "update" | "mismatch" | "unknown" | "stopped";
export type PartKind = "service" | "agent" | "cluster" | "cli";

export interface ReleaseFix {
  /** An advisory id, e.g. FSA-2026-001. */
  id: string;
  severity: string;
  summary: string;
}

export interface ReleaseInfo {
  version: string;
  channel: string;
  /** YYYY-MM-DD */
  date: string;
  severity: UpdateSeverity;
  summary: string;
  notes_url: string;
  /** The oldest release that can upgrade straight to this one; null: any. */
  upgrade_from: string | null;
  fixes: ReleaseFix[];
  /** Versions of the parts released with it: `cli`, `agent`. */
  components: Record<string, string>;
}

export interface UpdateCheckInfo {
  enabled: boolean;
  channel: string;
  feed_url: string;
  /** Where the kept release list came from ("url", or an upload); null before the first. */
  source: string | null;
  checked_at: string | null;
  succeeded_at: string | null;
  feed_generated_at: string | null;
  /** Why the last check failed; null when it worked. */
  error: string | null;
  /** The check has not worked for a while. */
  failing: boolean;
}

/** Just enough for the banner on admin pages. */
export interface UpdateNotice {
  current: string;
  latest: string | null;
  severity: UpdateSeverity;
  update_available: boolean;
  unsupported: boolean;
  supported_until: string | null;
}

export interface InstallPart {
  kind: PartKind;
  name: string;
  /** What it runs; null when it cannot be told. */
  running: string | null;
  /** What this install's release expects it to run. */
  expected: string | null;
  status: PartStatus;
  detail: string | null;
  /** A console page about it. */
  link: string | null;
}

export interface CurrentInstall {
  release: string;
  backend?: string | null;
  installer?: string | null;
}

export interface UpdatesOverview extends Omit<UpdateNotice, "current"> {
  check: UpdateCheckInfo;
  /** The install's release; older answers sent just the version. */
  current: CurrentInstall | string;
  latest_release: ReleaseInfo | null;
  /** Newer releases on the channel, newest first. */
  newer: ReleaseInfo[];
  /** Releases to install one after the other to reach the newest. */
  path: string[];
  /** What to run on the server for the next step on the path. */
  commands: string[];
  /** The agent version the newest release brings, when it names one. */
  released_agent: string | null;
  parts: InstallPart[];
  docker_error: string | null;
}

/** The release this server runs, whichever shape the overview sent it in. */
export function currentRelease(overview: Pick<UpdatesOverview, "current">): string {
  return typeof overview.current === "string" ? overview.current : overview.current.release;
}

export const updatesApi = {
  overview() {
    return request<UpdatesOverview>("");
  },
  notice() {
    return request<UpdateNotice>("/notice");
  },
  /** Read the release list now (also when the daily check is off); the overview comes back. */
  check() {
    return request<UpdatesOverview>("/check", json("POST"));
  },
  /** Keep a release list copied by hand: index.json and index.json.sig, as published. */
  uploadFeed(index: File, signature: File) {
    const form = new FormData();
    form.append("index", index);
    form.append("signature", signature);
    return request<UpdatesOverview>("/feed", { method: "POST", body: form });
  },
};
