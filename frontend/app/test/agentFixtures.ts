/** Agents as the API returns them, for page tests. */
import type { Agent, AgentVersion } from "~/api/agents";

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
    extensions: {},
    created_at: "2026-09-30T10:00:00Z",
    published_at: version === 1 ? "2026-09-30T10:05:00Z" : null,
    created_by: null,
    ...overrides,
  };
}
