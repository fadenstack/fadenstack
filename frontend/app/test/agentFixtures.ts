/** Agents as the API returns them, for page tests. */
import type { Agent, AgentVersion, AppSeen, AppsReview, ToolReview } from "~/api/agents";

export function toolReviewFixture(name: string, overrides: Partial<ToolReview> = {}): ToolReview {
  return {
    name,
    status: "new",
    suggested_class: "read",
    contract_class: null,
    in_contract: false,
    description: null,
    schema_sha256: null,
    contract_schema_sha256: null,
    open_world: null,
    apps: ["Excel add-in 2.0"],
    last_seen_at: "2026-10-04T10:00:00Z",
    warnings: [],
    ...overrides,
  };
}

export function appSeenFixture(overrides: Partial<AppSeen> = {}): AppSeen {
  return {
    source: "app",
    name: "Excel add-in",
    version: "2.0",
    sdk: "dotnet 0.4.0",
    first_seen_at: "2026-10-01T10:00:00Z",
    last_seen_at: "2026-10-04T10:00:00Z",
    reports: 12,
    last_user_id: "u-1",
    last_user_label: "jane@plant.example",
    tools: ["read_range", "write_range", "delete_sheet"],
    context: [{ id: "workbook:selection", title: "The selected range" }],
    ...overrides,
  };
}

/** What the apps bring: nothing reported unless the overrides say. */
export function appsReviewFixture(overrides: Partial<AppsReview> = {}): AppsReview {
  return {
    compared_with: 1,
    unrestricted: false,
    apps: [],
    tools: [],
    ui: null,
    pending: 0,
    published_version: 1,
    live_pending: 0,
    ...overrides,
  };
}

export function agentFixture(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "a-1",
    tenant_id: "default",
    slug: "excel-assistant",
    name: "Excel assistant",
    description: "Works on the open workbook.",
    status: "published",
    published_version: 1,
    latest_version: 1,
    can_manage: true,
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
    ...overrides,
  };
}

export function versionFixture(version: number, overrides: Partial<AgentVersion> = {}): AgentVersion {
  return {
    id: `v-${version}`,
    version,
    instructions: `Instructions v${version}`,
    model_alias: null,
    knowledge: [],
    server_tools: null,
    host_tool_contract: [{ name: "read_range", risk: "read" }],
    local_mcp_mode: "disable",
    ui: {},
    pii: { allow_mode_override: null },
    extensions: {},
    created_at: "2026-09-30T10:00:00Z",
    published_at: version === 1 ? "2026-09-30T10:05:00Z" : null,
    created_by: null,
    ...overrides,
  };
}
