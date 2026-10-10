/**
 * The update notice: whether a newer Fadenstack release is out, for the banner
 * on admin pages and the sidebar's dot beside Updates.
 *
 * One shared query for both (and the Updates page, which refreshes it after a
 * check), read every ten minutes: the server keeps the release list it read
 * last, so asking is cheap, and the list itself changes once a day at most.
 */
import { updatesApi, type UpdateNotice } from "~/api/updates";
import { useDashboardQuery } from "~/lib/dashboardQuery";

export const UPDATE_NOTICE_REFRESH_MS = 10 * 60_000;
const NOTICE_KEY = "admin/updates/notice";

export function useUpdateNotice(enabled = true) {
  return useDashboardQuery<UpdateNotice>(NOTICE_KEY, () => updatesApi.notice(), {
    refreshMs: UPDATE_NOTICE_REFRESH_MS,
    enabled,
  });
}

/** A day (YYYY-MM-DD, as the release list has them) or a timestamp, in the UI language. */
export function fmtDay(value: string | null | undefined, lang?: string): string {
  if (!value) return "–";
  const dayOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const ms = Date.parse(dayOnly ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(ms)) return value;
  try {
    // A bare day is that day everywhere: read and shown in UTC, never a day off.
    return new Intl.DateTimeFormat(lang, { dateStyle: "medium", ...(dayOnly ? { timeZone: "UTC" } : {}) }).format(ms);
  } catch {
    return value;
  }
}

/** Which release's security banner the viewer hid, in this browser. */
export const BANNER_DISMISSED_KEY = "faden-update-banner-dismissed";

export function readDismissedVersion(): string | null {
  try {
    return window.localStorage.getItem(BANNER_DISMISSED_KEY);
  } catch {
    return null;
  }
}

export function writeDismissedVersion(version: string): void {
  try {
    window.localStorage.setItem(BANNER_DISMISSED_KEY, version);
  } catch {
    // Storage refused (private mode, quota): the banner just comes back next time.
  }
}

export type UpdateBannerKind = "critical" | "security" | "unsupported";

export interface UpdateBannerState {
  kind: UpdateBannerKind;
  /** Only a security update can be hidden, and only until the next release. */
  dismissible: boolean;
  /** Also say that this release is past its support (with a critical or security update). */
  unsupported: boolean;
}

/**
 * Whether the banner shows, and how: for a critical or security update and for
 * a release past its support. A plain update is the sidebar's dot and the
 * dashboard's business, not a banner on every page.
 */
export function updateBannerState(
  notice: UpdateNotice | null | undefined,
  dismissedVersion: string | null,
): UpdateBannerState | null {
  if (!notice) return null;
  const urgent =
    notice.update_available && (notice.severity === "critical" || notice.severity === "security")
      ? notice.severity
      : null;
  if (urgent === "critical") return { kind: "critical", dismissible: false, unsupported: notice.unsupported };
  if (notice.unsupported) {
    // Past its support: never hidden, whatever else is said with it.
    return urgent === "security"
      ? { kind: "security", dismissible: false, unsupported: true }
      : { kind: "unsupported", dismissible: false, unsupported: true };
  }
  if (urgent === "security") {
    if (notice.latest && dismissedVersion === notice.latest) return null;
    return { kind: "security", dismissible: true, unsupported: false };
  }
  return null;
}
