/**
 * MCP Tool Registry API — manages MCP servers and their tools.
 * Proxied through the backend admin API.
 */

const BASE = "/api/admin/mcp";

// ── Types ──────────────────────────────────────────────────────────────────

export type MCPTransportType = "stdio" | "sse" | "streamable_http";
export type MCPServerStatus =
  | "registering"
  | "active"
  | "degraded"
  | "disconnected"
  | "error"
  | "disabled";
export type PIIMode = "allow" | "redact" | "block";

// Field names are the MCP service's own (faden_mcp web/api/admin/schemas.py).
// They were the database column names here -- command_json, headers_json and
// so on -- which the service ignores: a stdio server could not be registered
// from the console, and headers and env could never be sent.

export interface MCPServerSummary {
  id: string;
  name: string;
  transport: MCPTransportType;
  tool_prefix: string;
  status: MCPServerStatus;
  pii_mode: PIIMode;
  enabled: boolean;
  discovered_tools: number;
  has_settings: boolean;
  created_at: string;
  updated_at: string;
  last_discovery_at: string | null;
  /** Why the last connect or discovery failed, when it did. */
  last_error: string | null;
}

export interface MCPServerDetail extends MCPServerSummary {
  url: string | null;
  command: string[] | null;
  args: string[] | null;
  working_dir: string | null;
  timeout_sec: number;
  heartbeat_interval_sec: number;
  tenant_id: string;
  /** Which headers and environment variables are set; never their values. */
  header_names: string[];
  env_names: string[];
  /** On a register: why the server was saved but could not be connected. */
  warnings?: string[];
}

export interface MCPToolDetail {
  id: string;
  server_id: string;
  upstream_name: string;
  qualified_name: string;
  display_name: string | null;
  description: string;
  enabled: boolean;
  version: string;
  schema_hash: string;
  last_seen_at: string;
  input_schema: Record<string, unknown> | null;
  openai_schema: Record<string, unknown> | null;
}

export interface RegisterMCPServerPayload {
  name: string;
  transport: MCPTransportType;
  tool_prefix: string;
  url?: string;
  command?: string[];
  args?: string[];
  working_dir?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
  pii_mode?: PIIMode;
  timeout_sec?: number;
  heartbeat_interval_sec?: number;
  auto_discover?: boolean;
}

export interface UpdateMCPServerPayload {
  name?: string;
  url?: string;
  command?: string[];
  args?: string[];
  enabled?: boolean;
  pii_mode?: PIIMode;
  timeout_sec?: number;
  heartbeat_interval_sec?: number;
  /** Merged into what is stored: a value sets a key, null removes it, a key left out stays. */
  env?: Record<string, string | null>;
  headers?: Record<string, string | null>;
}

export interface UpdateMCPToolPayload {
  enabled?: boolean;
  display_name?: string | null;
}

export interface MCPTestResult {
  /** The server's health, or "error". */
  status: string;
  message: string;
  tools_found: number;
  tool_names?: string[];
}

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * The message in an error body: FastAPI's `detail`, not the JSON around it.
 *
 * The page showed `{"detail":"Internal Server Error"}` verbatim.
 */
export function errorMessage(body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    const detail = parsed.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : String(item)))
        .join("; ");
    }
  } catch {
    // Not JSON: the text itself.
  }
  return body;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(errorMessage(text) || `MCP API failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Servers ────────────────────────────────────────────────────────────────

export async function listServers(): Promise<MCPServerSummary[]> {
  return request<MCPServerSummary[]>("/servers");
}

export async function getServer(id: string): Promise<MCPServerDetail> {
  return request<MCPServerDetail>(`/servers/${encodeURIComponent(id)}`);
}

export async function registerServer(
  payload: RegisterMCPServerPayload,
): Promise<MCPServerDetail> {
  return request<MCPServerDetail>("/servers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateServer(
  id: string,
  payload: UpdateMCPServerPayload,
): Promise<MCPServerDetail> {
  return request<MCPServerDetail>(`/servers/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteServer(id: string): Promise<void> {
  return request<void>(`/servers/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function refreshServer(id: string): Promise<MCPServerDetail> {
  return request<MCPServerDetail>(
    `/servers/${encodeURIComponent(id)}/refresh`,
    { method: "POST" },
  );
}

export async function testServer(id: string): Promise<MCPTestResult> {
  return request<MCPTestResult>(`/servers/${encodeURIComponent(id)}/test`, {
    method: "POST",
  });
}

// ── Tools ──────────────────────────────────────────────────────────────────

export async function listServerTools(
  serverId: string,
): Promise<MCPToolDetail[]> {
  return request<MCPToolDetail[]>(
    `/servers/${encodeURIComponent(serverId)}/tools`,
  );
}

export async function updateTool(
  toolId: string,
  payload: UpdateMCPToolPayload,
): Promise<MCPToolDetail> {
  return request<MCPToolDetail>(`/tools/${encodeURIComponent(toolId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// ── Provider Settings ─────────────────────────────────────────────────────

export type JSONSchema = Record<string, unknown>;

export async function getSettingsSchema(serverId: string): Promise<JSONSchema> {
  return request<JSONSchema>(
    `/servers/${encodeURIComponent(serverId)}/settings/schema`,
  );
}

export async function getSettings(
  serverId: string,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `/servers/${encodeURIComponent(serverId)}/settings`,
  );
}

export async function updateSettings(
  serverId: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `/servers/${encodeURIComponent(serverId)}/settings`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

// ── Network Scanner ───────────────────────────────────────────────────────

export interface DiscoveredServer {
  host: string;
  port: number;
  url: string;
  server_name: string;
  protocol_version: string | null;
  tools: string[];
  already_registered: boolean;
}

export interface ScanResult {
  discovered: DiscoveredServer[];
  scanned_ports: number;
  errors: number;
}

export async function scanForServers(
  host: string,
  portStart: number = 8000,
  portEnd: number = 9000,
): Promise<ScanResult> {
  return request<ScanResult>("/scan", {
    method: "POST",
    body: JSON.stringify({
      host,
      port_start: portStart,
      port_end: portEnd,
    }),
  });
}
