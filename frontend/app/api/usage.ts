/**
 * Usage: what people used of the models, and the privacy rules around it.
 *
 * - `/api/usage/me`: the caller's own usage (every signed-in person, M03), the
 *   limits that apply and who may see their figures (M14); `/me/export` is the
 *   Art. 15 download (M13).
 * - `/api/admin/usage` (`usage:read`): totals and groups, every figure over at
 *   least k people (M04, M05); no person dimension (M11).
 * - `/api/admin/usage/people`: per-person figures of others, only with
 *   person-level reporting on, the usage auditor grant and a reason (M02, M06,
 *   M07); the server records each call.
 * - `/api/admin/usage/settings`, `/limits`, `/statement`: the privacy settings,
 *   the instance's caps, the configuration statement (S09).
 *
 * Refusals the console words itself carry a code (`USAGE_ERROR_CODES`);
 * a settings write the server rejects field by field carries `problems`.
 */
import { ApiError, query, readError } from "./http";

const ME = "/api/usage/me";
const ADMIN = "/api/admin/usage";

// ── Types ────────────────────────────────────────────────────────────────────

/** What every figure carries. `cost` per currency; `{}` when no price applies. */
export interface Measures {
  requests: number;
  /** Status 400 or above, refusals not counted. */
  failed: number;
  /** Refused by a limit or an admission check. */
  refused: number;
  /** All input, cached included. */
  input_tokens: number;
  cached_input_tokens: number;
  /** All output, reasoning included. */
  output_tokens: number;
  reasoning_tokens: number;
  cost: Record<string, number>;
}

export type MyBucket = "day" | "month";
export type AdminBucket = "hour" | "day" | "month";
export type GroupBy = "team" | "agent" | "model" | "provider";
export type Granularity = "day" | "month";

/** One period of a series: a date (`YYYY-MM-DD`, usage time zone) or an hour (ISO-8601 UTC). */
export interface UsagePeriod extends Measures {
  period: string;
}

/** One row of a breakdown; `key` null: none (no agent, no project). */
export interface UsageBreakdown extends Measures {
  key: string | null;
  label: string | null;
}

export type CapKind = "rpm" | "tpm" | "tokens_per_day";

/** A limit of the instance and where it stands now. */
export interface UsageCap {
  kind: CapKind;
  limit: number;
  used: number;
  resets_at: string | null;
}

/** A block an extension adds (budgets, later): a kind and its fields. */
export interface UsageSection {
  kind: string;
  data: Record<string, unknown>;
}

/** Who may see the caller's figures, for the notice on their page (M14). */
export interface MyPrivacy {
  person_reporting: boolean;
  granularity: Granularity;
  retention_days: number;
  /** Audited views of the caller's figures by others. */
  views_of_my_data: number;
}

export interface MyUsage {
  time_zone: string;
  from: string;
  to: string;
  bucket: MyBucket;
  totals: Measures;
  series: UsagePeriod[];
  by_model: UsageBreakdown[];
  by_agent: UsageBreakdown[];
  by_project: UsageBreakdown[];
  caps: UsageCap[];
  sections: UsageSection[];
  privacy: MyPrivacy;
}

/** A period of the admin series; `suppressed`: fewer than k people, no measures to show. */
export interface AdminUsagePeriod extends UsagePeriod {
  suppressed: boolean;
}

/**
 * A group of the admin view. `other`: the one row the groups with fewer than
 * k people were merged into (it holds at least k itself); its `people` is null.
 */
export interface AdminUsageGroup extends Measures {
  key: string | null;
  label: string | null;
  other?: boolean;
  people: number | null;
  suppressed: boolean;
}

export interface AdminUsage {
  time_zone: string;
  from: string;
  to: string;
  bucket: AdminBucket;
  group_by: GroupBy;
  /** k: the fewest people a shown figure covers. */
  min_group_size: number;
  /** Fewer than k active people on the instance (S12). */
  small_instance: boolean;
  /** `people` null below k. */
  totals: Measures & { people: number | null };
  series: AdminUsagePeriod[];
  groups: AdminUsageGroup[];
  person_reporting: boolean;
  /** Person-level reporting is on and the caller holds `usage.people:audit`. */
  can_view_people: boolean;
}

/** Whose per-person figures: one team or one person, and why. */
export interface PeopleQuery {
  from?: string;
  to?: string;
  team?: string;
  person?: string;
  /** 10 to 500 characters; recorded with the view. */
  reason: string;
}

export interface PersonUsageRow extends Measures {
  person: { id: string; name: string | null; email: string };
  period: string;
}

/** Rows sorted by name, then period -- never by usage (M11). */
export interface PeopleUsage {
  granularity: Granularity;
  rows: PersonUsageRow[];
}

/** The `usage.*` system settings, without the prefix (C2). */
export interface UsageSettings {
  person_reporting: boolean;
  min_group_size: number;
  person_granularity: Granularity;
  request_log_retention_days: number;
  person_retention_days: number;
  hourly_retention_days: number;
  daily_retention_months: number;
  time_zone: string;
}

/** The instance's caps; null: no limit. */
export interface UsageLimits {
  rpm_limit: number | null;
  tpm_limit: number | null;
  tokens_per_day: number | null;
}

// ── Rules shared with the backend ────────────────────────────────────────────

/** The ranges the backend enforces (C2); the form checks them before it sends. */
export const USAGE_SETTING_RANGES = {
  min_group_size: { min: 5, max: 50 },
  request_log_retention_days: { min: 7, max: 365 },
  person_retention_days: { min: 31, max: 400 },
  hourly_retention_days: { min: 31, max: 180 },
  daily_retention_months: { min: 13, max: 60 },
} as const;

export type RangedSetting = keyof typeof USAGE_SETTING_RANGES;

/** Past this, keeping per-person figures needs a documented reason (S06). */
export const PERSON_RETENTION_WARN_DAYS = 90;

/** A reason for a per-person view: at least this long, at most `REASON_MAX` (M07). */
export const REASON_MIN = 10;
export const REASON_MAX = 500;

/** Whether a reason may be sent: 10 to 500 characters, spaces at the ends not counted. */
export function reasonOk(reason: string): boolean {
  const n = reason.trim().length;
  return n >= REASON_MIN && n <= REASON_MAX;
}

/** The refusals the console words itself (`usage.errors.<code>`). */
export const USAGE_ERROR_CODES = [
  "person_reporting_off",
  "not_usage_auditor",
  "reason_required",
  "root_mode",
] as const;

export type UsageErrorCode = (typeof USAGE_ERROR_CODES)[number];

export function isUsageErrorCode(code: string | undefined): code is UsageErrorCode {
  return (USAGE_ERROR_CODES as readonly string[]).includes(code ?? "");
}

/** One field the server rejected. */
export interface SettingsProblem {
  field: string;
  code: "out_of_range" | "invalid" | (string & {});
}

/** A 422 that names the fields: `{"detail": {"problems": [...]}}`. */
export class UsageProblemsError extends ApiError {
  constructor(readonly problems: SettingsProblem[]) {
    super(problems.map((p) => `${p.field}: ${p.code}`).join("; "), 422);
    this.name = "UsageProblemsError";
  }
}

// ── Calls ────────────────────────────────────────────────────────────────────

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    credentials: "include",
  });
  if (!res.ok) {
    const problems = await res
      .clone()
      .json()
      .then((body: { detail?: { problems?: SettingsProblem[] } }) => body?.detail?.problems)
      .catch(() => undefined);
    if (Array.isArray(problems)) throw new UsageProblemsError(problems);
    throw await readError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function put<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, { method: "PUT", body: JSON.stringify(body) });
}

export interface RangeParams {
  from?: string;
  to?: string;
}

export const usageApi = {
  /** The caller's own usage; the server's default is the last 30 days, per day. */
  me(params: RangeParams & { bucket?: MyBucket } = {}) {
    return request<MyUsage>(`${ME}${query({ ...params })}`);
  },

  /** Where the browser downloads the caller's own data (rollups, request records, views of it). */
  meExportUrl(format: "json" | "csv") {
    return `${ME}/export${query({ format })}`;
  },

  /** Totals and groups, k-suppressed. */
  admin(params: RangeParams & { bucket?: AdminBucket; group_by?: GroupBy } = {}) {
    return request<AdminUsage>(`${ADMIN}${query({ ...params })}`);
  },

  /** Per-person figures of a team or a person; refused with a code unless allowed. */
  people(params: PeopleQuery) {
    return request<PeopleUsage>(`${ADMIN}/people${query({ ...params })}`);
  },

  settings() {
    return request<UsageSettings>(`${ADMIN}/settings`);
  },

  /** Only the fields given change; `person_reporting` needs root mode. */
  updateSettings(changes: Partial<UsageSettings>) {
    return put<UsageSettings>(`${ADMIN}/settings`, changes);
  },

  limits() {
    return request<UsageLimits>(`${ADMIN}/limits`);
  },

  updateLimits(limits: UsageLimits) {
    return put<UsageLimits>(`${ADMIN}/limits`, limits);
  },

  /** Where the browser downloads the configuration statement (Markdown). */
  statementUrl(language: string) {
    return `${ADMIN}/statement${query({ language })}`;
  },
};
