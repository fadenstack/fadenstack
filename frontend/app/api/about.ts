/** `GET /api/about`: what the About dialog shows, for anyone signed in. */
import { requester } from "./http";

const request = requester("/api");

export interface AboutInfo {
  product: string;
  /** "core" unless Enterprise says otherwise. */
  edition: string;
  /** The release `faden` installed; empty for an install built from a source checkout. */
  version: string;
  licence: string;
  /** Whether the links back to fadenstack.com show (only an edition other than core switches them off). */
  vendor_links: boolean;
  /** What this server adds beyond core, by name; a feature's screens show only while it is listed. */
  features?: string[];
}

/** Whether the server adds *feature* (``GET /api/about``); false while unknown. */
export async function hasFeature(feature: string): Promise<boolean> {
  try {
    return ((await aboutInfo()).features ?? []).includes(feature);
  } catch {
    return false;
  }
}

let cached: Promise<AboutInfo> | null = null;

/** The facts, asked for once per page load (they change only with an upgrade, which reloads the page). */
export function aboutInfo(): Promise<AboutInfo> {
  if (!cached) {
    cached = request<AboutInfo>("/about");
    cached.catch(() => {
      cached = null;
    });
  }
  return cached;
}

/** For tests: forget the cached answer. */
export function forgetAboutInfo(): void {
  cached = null;
}
