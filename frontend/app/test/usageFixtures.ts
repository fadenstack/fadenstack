/** Usage API payloads (C5) for page tests. */
import type { AdminUsage, Measures, MyUsage, UsageLimits, UsageSettings } from "~/api/usage";

export function measures(overrides: Partial<Measures> = {}): Measures {
  return {
    requests: 1234,
    failed: 3,
    refused: 2,
    input_tokens: 50000,
    cached_input_tokens: 12000,
    output_tokens: 8000,
    reasoning_tokens: 1500,
    cost: { EUR: 1.25 },
    ...overrides,
  };
}

export function myUsage(overrides: Partial<MyUsage> = {}): MyUsage {
  return {
    time_zone: "Europe/Berlin",
    from: "2026-09-29",
    to: "2026-10-28",
    bucket: "day",
    totals: measures(),
    series: [
      { period: "2026-10-27", ...measures({ requests: 600 }) },
      { period: "2026-10-28", ...measures({ requests: 634 }) },
    ],
    by_model: [{ key: "qwen3.8-27b-fp8", label: "qwen3.8-27b-fp8", ...measures() }],
    by_agent: [{ key: null, label: null, ...measures({ requests: 1000 }) }],
    by_project: [],
    caps: [{ kind: "tokens_per_day", limit: 1000, used: 120, resets_at: "2026-10-28T22:00:00Z" }],
    sections: [],
    privacy: { person_reporting: false, granularity: "day", retention_days: 90, views_of_my_data: 0 },
    ...overrides,
  };
}

export function adminUsage(overrides: Partial<AdminUsage> = {}): AdminUsage {
  return {
    time_zone: "Europe/Berlin",
    from: "2026-09-29",
    to: "2026-10-28",
    bucket: "day",
    group_by: "team",
    min_group_size: 5,
    small_instance: false,
    totals: { ...measures(), people: 21 },
    series: [{ period: "2026-10-28", suppressed: false, ...measures() }],
    groups: [
      { key: "g-sales", label: "Sales", people: 7, suppressed: false, ...measures({ requests: 70 }) },
      { key: "g-eng", label: "Engineering", people: 9, suppressed: false, ...measures({ requests: 900 }) },
      { key: null, label: null, other: true, people: null, suppressed: true, ...measures({ requests: 30 }) },
    ],
    person_reporting: false,
    can_view_people: false,
    ...overrides,
  };
}

export function usageSettings(overrides: Partial<UsageSettings> = {}): UsageSettings {
  return {
    person_reporting: false,
    min_group_size: 5,
    person_granularity: "day",
    request_log_retention_days: 90,
    person_retention_days: 90,
    hourly_retention_days: 90,
    daily_retention_months: 25,
    time_zone: "Europe/Berlin",
    ...overrides,
  };
}

export function usageLimits(overrides: Partial<UsageLimits> = {}): UsageLimits {
  return { rpm_limit: null, tpm_limit: null, tokens_per_day: null, ...overrides };
}
