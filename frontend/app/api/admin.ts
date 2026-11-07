/**
 * Admin API client — thin fetch wrappers for all /api/admin endpoints.
 *
 * The backend is the authority on capabilities; the UI reads allowed actions
 * from the container's container_class and the root-mode status, exactly
 * mirroring the server-side matrix.
 */

import { clearCachedAccess } from "~/lib/adminConstants";
import { AGENT_INSPECTOR, AGENT_MAINTAINER } from "./agents";

const BASE = "/api/admin";

// ─────────────────────────────────────────────────────────────────────────────
// Types (mirror backend schemas)
// ─────────────────────────────────────────────────────────────────────────────

export type ContainerClass =
  | "SYSTEM_CORE"
  | "SYSTEM_AUX"
  | "MCP"
  | "TENANT_APP"
  | "UNTRUSTED";
export type ContainerPolicy = "locked" | "restricted" | "free";

export interface ContainerSummary {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
  created: string;
  ports: Record<string, unknown>[];
  networks: string[];
  endpoint: string;
  container_class: ContainerClass;
  policy: ContainerPolicy;
  owner_scope: string;
}

/** A container's detail page: no environment or command line (they hold the platform's keys). */
export type ContainerDetail = ContainerSummary;

export interface PortBinding {
  host_port: string;
  container_port: string;
}

export interface CreateContainerPayload {
  image: string;
  name?: string;
  container_class?: ContainerClass;
  owner_scope?: string;
  policy?: ContainerPolicy;
  auto_start?: boolean;
  ports?: PortBinding[];
  env?: string[];
  cmd?: string[];
  network?: string;
  volumes?: string[];
}

/** A container as its edit form shows it: what it was created with, not what its image supplies. */
export interface ContainerSettings {
  image: string;
  name: string;
  /** Secret flag values hidden. */
  cmd: string[] | null;
  /** KEY=VALUE; secret values hidden. Sent back unchanged, a hidden value is kept. */
  env: string[];
  /** Names in `env` whose value (or a URL's password) is hidden. */
  hidden_env: string[];
  /** A value in `cmd` is hidden. */
  cmd_hidden: boolean;
  ports: PortBinding[];
  volumes: string[];
  network: string;
  running: boolean;
  container_class: ContainerClass;
  owner_scope: string;
  policy: ContainerPolicy;
  /** Made by Compose: the next deploy or upgrade makes it again from the compose file. */
  managed_by_compose: boolean;
  /** The backend runs in it and cannot replace it. */
  backend_runs_here: boolean;
}

export interface ImageSummary {
  id: string;
  repo_tags: string[];
  repo_digests: string[];
  size: number;
  created: string;
}

export interface PruneReport {
  deleted: string[];
  space_reclaimed: number;
  dry_run: boolean;
}

export interface ImageCheckResponse {
  exists: boolean;
  image: string;
  tag: string;
  pulling: boolean;
  pull_id: string | null;
}

export interface PullStartedResponse {
  pull_id: string;
  image: string;
  tag: string;
  already_pulling: boolean;
}

export interface PullProgressEvent {
  pull_id: string;
  image: string;
  state: "pulling" | "complete" | "failed";
  percent?: number;
  current_bytes?: number;
  total_bytes?: number;
  layers_done?: number;
  layers_total?: number;
  error?: string;
}

export interface StackRevision {
  id: string;
  stack_id: string;
  rev: number;
  compose_yaml: string;
  env_blob: string | null;
  image_digests: string | null;
  created_by: string | null;
  created_at: string;
}

export interface StackSummary {
  stack_id: string;
  latest_rev: number;
  created_at: string;
}

export interface StackDiff {
  stack_id: string;
  from_rev: number;
  to_rev: number;
  compose_yaml_from: string;
  compose_yaml_to: string;
  env_blob_from: string | null;
  env_blob_to: string | null;
  image_digests_from: string | null;
  image_digests_to: string | null;
}

export interface RootSession {
  id: string;
  actor_id: string;
  start_time: string;
  end_time: string | null;
  reason: string;
  scope: string;
  duration_seconds: number;
  active: boolean;
}

export interface RootModeStatus {
  active: boolean;
  session: RootSession | null;
}

export interface AuditEvent {
  id: string;
  time: string;
  actor_id: string | null;
  /** Who acted, as written at the time (kept when the account is deleted). */
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string;
  result: "allow" | "deny";
  severity: string;
  metadata_json: string | null;
  client_ip: string | null;
  request_id: string | null;
  user_agent: string | null;
  /** console, api, cli or system. */
  source: string;
  hash: string | null;
}

/** Whether the audit trail is as it was written (its hash chain and file copy). */
export interface AuditChain {
  ok: boolean;
  events: number;
  legacy_rows: number;
  intact_until: string | null;
  problems: string[];
}

export interface AuditFilters {
  actor_email?: string;
  action?: string;
  target_id?: string;
  since?: string;
  until?: string;
  result?: "allow" | "deny";
  severity?: string;
  source?: string;
}

export interface DashboardTopUser {
  container_id: string;
  name: string;
  value: number;
  unit: string;
}

export interface DashboardOverview {
  system_status: string;
  system_badge: string;

  cpu_percent: number | null;
  load_1m: number | null;
  load_5m: number | null;
  load_15m: number | null;

  ram_used_bytes: number | null;
  ram_total_bytes: number | null;
  swap_used_bytes: number | null;
  swap_total_bytes: number | null;

  disk_free_bytes: number;
  disk_total_bytes: number;
  disk_free_percent: number;

  network_rx_bytes: number | null;
  network_tx_bytes: number | null;

  containers_running: number;
  containers_total: number;
  containers_restarting: number;
  restart_rate_1h: number;
  restart_rate_24h: number;

  api_error_rate_5xx: number;

  postgres_connections: number | null;
  postgres_max_connections: number | null;

  gpu_util_percent: number | null;
  gpu_vram_used_bytes: number | null;
  gpu_vram_total_bytes: number | null;
}

export interface DashboardTopContainers {
  top_cpu_containers: DashboardTopUser[];
  top_memory_containers: DashboardTopUser[];
}

export interface DashboardHealthItem {
  name: string;
  status: string;
  detail: string | null;
}

export interface DashboardHealth {
  overall_status: string;
  items: DashboardHealthItem[];
}

export interface GrafanaPanel {
  panel_id: number;
  title: string;
  embed_url: string;
}

export interface GrafanaPanels {
  enabled: boolean;
  grafana_url: string | null;
  dashboard_uid: string | null;
  open_dashboard_url: string | null;
  panels: GrafanaPanel[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      Pragma: "no-cache",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearCachedAccess();
    }
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Containers
// ─────────────────────────────────────────────────────────────────────────────

export const containers = {
  list(filterClass?: ContainerClass) {
    const qs = filterClass ? `?class=${filterClass}` : "";
    return request<ContainerSummary[]>(`/containers/${qs}`);
  },
  create(payload: CreateContainerPayload) {
    return request<ContainerSummary>("/containers/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  get(id: string) {
    return request<ContainerDetail>(`/containers/${id}`);
  },
  settings(id: string) {
    return request<ContainerSettings>(`/containers/${id}/settings`);
  },
  /** Replaces the container with one made from the edits; the result has a new id. */
  update(id: string, payload: CreateContainerPayload) {
    return request<ContainerSummary>(`/containers/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  lifecycle(
    id: string,
    action: "start" | "stop" | "restart" | "pause" | "unpause",
  ) {
    return request<void>(`/containers/${id}/${action}`, { method: "POST" });
  },
  logs(id: string, tail = 200): EventSource {
    return new EventSource(`${BASE}/containers/${id}/logs?tail=${tail}`, {
      withCredentials: true,
    });
  },
  fetchLogs(id: string, tail = 200): Promise<Response> {
    return fetch(`${BASE}/containers/${id}/logs?tail=${tail}`, {
      credentials: "include",
    });
  },
  delete(id: string, force = false) {
    return request<void>(`/containers/${id}?force=${force}`, {
      method: "DELETE",
    });
  },
  register(
    id: string,
    classification: {
      container_class: ContainerClass;
      owner_scope: string;
      policy: ContainerPolicy;
    },
  ) {
    return request<ContainerSummary>(`/containers/${id}/register`, {
      method: "PUT",
      body: JSON.stringify(classification),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Images
// ─────────────────────────────────────────────────────────────────────────────

export const images = {
  list() {
    return request<ImageSummary[]>("/images/");
  },
  check(image: string, tag = "latest") {
    return request<ImageCheckResponse>(
      `/images/check?image=${encodeURIComponent(image)}&tag=${encodeURIComponent(tag)}`,
    );
  },
  pull(image: string, tag = "latest") {
    return request<PullStartedResponse>("/images/pull", {
      method: "POST",
      body: JSON.stringify({ image, tag }),
    });
  },
  /** Open an SSE stream for pull progress. Returns an EventSource. */
  pullProgress(
    pullId: string,
    onProgress: (data: PullProgressEvent) => void,
    onComplete: (data: PullProgressEvent) => void,
    onError: (data: PullProgressEvent | null) => void,
  ): EventSource {
    const source = new EventSource(`${BASE}/images/pull/${pullId}/progress`, {
      withCredentials: true,
    });
    source.addEventListener("progress", (raw) => {
      const data = JSON.parse(
        (raw as MessageEvent<string>).data,
      ) as PullProgressEvent;
      onProgress(data);
    });
    source.addEventListener("complete", (raw) => {
      const data = JSON.parse(
        (raw as MessageEvent<string>).data,
      ) as PullProgressEvent;
      onComplete(data);
      source.close();
    });
    source.addEventListener("error", (raw) => {
      if (raw instanceof MessageEvent) {
        const data = JSON.parse(raw.data as string) as PullProgressEvent;
        onError(data);
      } else {
        onError(null);
      }
      source.close();
    });
    return source;
  },
  prune(dry_run = false) {
    return request<PruneReport>("/images/prune", {
      method: "POST",
      body: JSON.stringify({ dry_run }),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Stacks
// ─────────────────────────────────────────────────────────────────────────────

export const stacks = {
  list() {
    return request<StackSummary[]>("/stacks/");
  },
  deploy(payload: {
    stack_id: string;
    compose_yaml: string;
    env_blob?: string | null;
    image_digests?: string | null;
  }) {
    return request<StackRevision>("/stacks/deploy", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  update(
    stackId: string,
    payload: {
      stack_id: string;
      compose_yaml: string;
      env_blob?: string | null;
      image_digests?: string | null;
    },
  ) {
    return request<StackRevision>(`/stacks/${stackId}/update`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  rollback(stackId: string, rev: number) {
    return request<StackRevision>(`/stacks/${stackId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ rev }),
    });
  },
  revisions(stackId: string) {
    return request<StackRevision[]>(`/stacks/${stackId}/revisions`);
  },
  diff(stackId: string, fromRev: number, toRev: number) {
    return request<StackDiff>(
      `/stacks/${stackId}/diff?from_rev=${fromRev}&to_rev=${toRev}`,
    );
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Root Mode
// ─────────────────────────────────────────────────────────────────────────────

export const rootMode = {
  status() {
    return request<RootModeStatus>("/root-mode/status");
  },
  start(reason: string, scope = "all", duration_seconds = 600) {
    return request<RootSession>("/root-mode/start", {
      method: "POST",
      body: JSON.stringify({ reason, scope, duration_seconds }),
    });
  },
  stop() {
    return request<RootSession>("/root-mode/stop", { method: "POST" });
  },
};

export const dashboard = {
  overview() {
    return request<DashboardOverview>("/dashboard/overview");
  },
  topContainers() {
    return request<DashboardTopContainers>("/dashboard/top-containers");
  },
  health() {
    return request<DashboardHealth>("/dashboard/health");
  },
  grafanaPanels() {
    return request<GrafanaPanels>("/dashboard/grafana/panels");
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Networks
// ─────────────────────────────────────────────────────────────────────────────

export interface NetworkContainer {
  id: string;
  name: string;
  ipv4_address: string;
  mac_address: string;
}

export interface NetworkSummary {
  id: string;
  name: string;
  driver: string;
  scope: string;
  internal: boolean;
  created: string;
  is_system: boolean;
  container_count: number;
}

export interface NetworkDetail extends NetworkSummary {
  subnet: string;
  gateway: string;
  containers: NetworkContainer[];
  labels: Record<string, string>;
  options: Record<string, string>;
}

export interface CreateNetworkPayload {
  name: string;
  driver?: string;
  internal?: boolean;
  subnet?: string | null;
  gateway?: string | null;
  labels?: Record<string, string>;
}

export const networks = {
  list() {
    return request<NetworkSummary[]>("/networks/");
  },
  get(id: string) {
    return request<NetworkDetail>(`/networks/${id}`);
  },
  create(payload: CreateNetworkPayload) {
    return request<NetworkDetail>("/networks/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  delete(id: string) {
    return request<void>(`/networks/${id}`, { method: "DELETE" });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Audit log
// ─────────────────────────────────────────────────────────────────────────────

function auditQuery(params: AuditFilters & { actor_id?: string; limit?: number; offset?: number } = {}): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") qs.set(key, String(value));
  }
  return qs.toString();
}

export const audit = {
  list(params?: AuditFilters & { actor_id?: string; limit?: number; offset?: number }) {
    const q = auditQuery(params);
    return request<AuditEvent[]>(`/audit/${q ? `?${q}` : ""}`);
  },
  /** Whether every event is as it was written. */
  chain() {
    return request<AuditChain>("/audit/chain");
  },
  /** The address of the CSV export for these filters (the export itself is recorded). */
  exportUrl(params?: AuditFilters) {
    const q = auditQuery(params);
    return `/api/admin/audit/export.csv${q ? `?${q}` : ""}`;
  },
};

export interface RbacPermission {
  id: string;
  resource: string;
  action: string;
}

export interface RbacRole {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  created_at: string;
  permissions: RbacPermission[];
  user_count: number;
}

export interface AdminUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  usage_group_id: string | null;
  usage_group_name: string | null;
  roles: RbacRole[];
  permissions: RbacPermission[];
  /** Agents may attach any local MCP server on this user's machines. */
  developer_mode: boolean;
  /** When developer mode ends by itself; null: until it is switched off. */
  developer_mode_until: string | null;
}

/** Developer mode is on, and its end (if any) has not passed. */
export function hasDeveloperMode(user: Pick<AdminUser, "developer_mode" | "developer_mode_until">, now = Date.now()) {
  return user.developer_mode && (!user.developer_mode_until || Date.parse(user.developer_mode_until) > now);
}

export interface MeAccess {
  id: string;
  email: string;
  is_superuser: boolean;
  roles: RbacRole[];
  permissions: RbacPermission[];
  /** Maintains at least one agent. */
  maintains_agents?: boolean;
  /** Sees the details of at least one agent as a member in another role (e.g. auditor). */
  inspects_agents?: boolean;
}

/** The keys the UI gates on: `resource:action` per permission, and the rights that are not permissions. */
export function accessKeys(access: Pick<MeAccess, "permissions" | "maintains_agents" | "inspects_agents">): string[] {
  const keys = access.permissions.map((p) => `${p.resource}:${p.action}`);
  if (access.maintains_agents) keys.push(AGENT_MAINTAINER);
  if (access.inspects_agents) keys.push(AGENT_INSPECTOR);
  return keys;
}

export const adminUsers = {
  meAccess() {
    return request<MeAccess>("/users/me/access");
  },
  list() {
    return request<AdminUser[]>("/users/");
  },
  listRoles() {
    return request<RbacRole[]>("/users/roles");
  },
  getRole(roleId: string) {
    return request<RbacRole>(`/users/roles/${roleId}`);
  },
  createRole(payload: {
    name: string;
    description?: string;
    permission_ids: string[];
  }) {
    return request<RbacRole>("/users/roles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateRole(
    roleId: string,
    payload: { name?: string; description?: string; permission_ids?: string[] },
  ) {
    return request<RbacRole>(`/users/roles/${roleId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  deleteRole(roleId: string) {
    return request<void>(`/users/roles/${roleId}`, { method: "DELETE" });
  },
  listPermissions() {
    return request<RbacPermission[]>("/users/permissions");
  },
  setUserRoles(userId: string, roleIds: string[]) {
    return request<AdminUser>(`/users/${userId}/roles`, {
      method: "PUT",
      body: JSON.stringify({ role_ids: roleIds }),
    });
  },
  /** Admins only, never the user: lets agents attach local MCP servers for this user. */
  setDeveloperMode(userId: string, enabled: boolean, until: string | null) {
    return request<{ user_id: string; developer_mode: boolean; developer_mode_until: string | null }>(
      `/users/${userId}/developer-mode`,
      { method: "PUT", body: JSON.stringify({ enabled, until }) },
    );
  },
  setUsageGroup(userId: string, groupId: string | null) {
    return request<AdminUser>(`/users/${userId}/usage-group`, {
      method: "PUT",
      body: JSON.stringify({ usage_group_id: groupId }),
    });
  },
  createUser(payload: {
    email: string;
    password: string;
    is_superuser?: boolean;
    role_ids?: string[];
  }) {
    return request<AdminUser>("/users/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  deleteUser(userId: string) {
    return request<void>(`/users/${userId}`, { method: "DELETE" });
  },
  /** Suspend an account (`false`) or let it back in (`true`); nothing of the person's is deleted. */
  setActive(userId: string, active: boolean) {
    return request<AdminUser>(`/users/${userId}/active`, { method: "PUT", body: JSON.stringify({ active }) });
  },
  /** Grant or remove the administrator right (Root Mode; recorded with the reason). */
  setAdministrator(userId: string, administrator: boolean, reason: string) {
    return request<AdminUser>(`/users/${userId}/administrator`, {
      method: "PUT",
      body: JSON.stringify({ administrator, reason }),
    });
  },
  /**
   * A one-time link that sets a new password for the account (Root Mode; recorded with the reason). The token
   * is in this answer only; the console makes the address from it.
   */
  createResetLink(userId: string, reason: string, validHours: 1 | 24) {
    return request<{ token: string; expires_at: string }>(`/users/${userId}/password-reset-link`, {
      method: "POST",
      body: JSON.stringify({ reason, valid_hours: validHours }),
    });
  },
  /** Move the administrator right or the auditor role from this account to another, in one step. */
  handOver(userId: string, right: "administrator" | "auditor", toUserId: string, reason: string) {
    return request<AdminUser>(`/users/${userId}/hand-over`, {
      method: "POST",
      body: JSON.stringify({ right, to_user_id: toUserId, reason }),
    });
  },
  changePassword(currentPassword: string, newPassword: string) {
    return request<void>("/users/me/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
  },
  /** A token for the gateway's API, in the account's tenant (the server sets it). */
  generateApiToken(expiresIn?: number, name?: string) {
    return request<{ token: string; expires_in: number | null; id: string; name: string | null; expires_at: string | null }>(
      "/users/me/api-token",
      {
        method: "POST",
        body: JSON.stringify({
          expires_in: expiresIn ?? null,
          name: name?.trim() || null,
        }),
      },
    );
  },
  /** The current user's API tokens, newest first; never the tokens themselves. */
  listMyApiTokens() {
    return request<ApiTokenInfo[]>("/users/me/api-tokens");
  },
  /** Revokes one of the current user's API tokens; the gateway refuses it at the next request. */
  revokeMyApiToken(tokenId: string) {
    return request<void>(`/users/me/api-tokens/${tokenId}`, { method: "DELETE" });
  },
  /** One of the current user's tokens' latest uses, newest first. */
  listMyApiTokenUses(tokenId: string) {
    return request<ApiTokenUse[]>(`/users/me/api-tokens/${tokenId}/uses`);
  },
  /** Someone's API tokens (administrators). */
  listUserApiTokens(userId: string) {
    return request<ApiTokenInfo[]>(`/users/${userId}/api-tokens`);
  },
  /** Revokes someone's API token (administrators). */
  revokeUserApiToken(userId: string, tokenId: string) {
    return request<void>(`/users/${userId}/api-tokens/${tokenId}`, { method: "DELETE" });
  },
  /** Everyone's API tokens with their owner and last use (administrators). */
  listAllApiTokens() {
    return request<AdminApiTokenInfo[]>("/users/api-tokens");
  },
  /** A token's latest uses, newest first (administrators). */
  listApiTokenUses(tokenId: string) {
    return request<ApiTokenUse[]>(`/users/api-tokens/${tokenId}/uses`);
  },
};

/** An API token as its owner sees it, with its latest use (within the kept period). */
export interface ApiTokenInfo {
  id: string;
  name: string | null;
  tenant_id: string;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at?: string | null;
  last_used_address?: string | null;
  last_used_client?: string | null;
  last_used_route?: string | null;
  /** Its uses in the last 30 days. */
  recent_uses?: number;
}

/** An API token in the overview of everyone's. */
export interface AdminApiTokenInfo extends ApiTokenInfo {
  user_id: string;
  email: string;
}

/** One use of an API token: each request the gateway let in with it. */
export interface ApiTokenUse {
  at: string;
  address: string | null;
  client: string | null;
  route: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Groups
// ─────────────────────────────────────────────────────────────────────────────

export interface GroupRole {
  id: string;
  name: string;
  description: string | null;
  is_builtin: boolean;
  created_at: string;
  permissions: RbacPermission[];
  user_count: number;
}

export interface Group {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  roles: GroupRole[];
  member_count: number;
  /** Who keeps the team: absent or null, an administrator; `provider:<key>`, that sign-in provider. */
  source?: string | null;
  /** The team above it; null for a top team (one parent; a path has at most six teams). */
  parent_id?: string | null;
  /** A finance code, free text up to 64 characters. */
  cost_center?: string | null;
  /** When the team was archived (it keeps its history; no new members; not a home team); null while active. */
  archived_at?: string | null;
}

/** The provider that keeps a team (`provider:<key>` → `<key>`), or null for an administrator's. */
export function teamKeeper(group: Pick<Group, "source">): string | null {
  return group.source?.startsWith("provider:") ? group.source.slice("provider:".length) : null;
}

export interface GroupMember {
  id: string;
  email: string;
}

export const adminGroups = {
  list() {
    return request<Group[]>("/groups/");
  },
  get(groupId: string) {
    return request<Group>(`/groups/${groupId}`);
  },
  create(payload: { name: string; description?: string; role_ids: string[] }) {
    return request<Group>("/groups/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  update(
    groupId: string,
    payload: { name?: string; description?: string; role_ids?: string[] },
  ) {
    return request<Group>(`/groups/${groupId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  delete(groupId: string) {
    return request<void>(`/groups/${groupId}`, { method: "DELETE" });
  },
  listMembers(groupId: string) {
    return request<GroupMember[]>(`/groups/${groupId}/members`);
  },
  setMembers(groupId: string, userIds: string[]) {
    return request<GroupMember[]>(`/groups/${groupId}/members`, {
      method: "PUT",
      body: JSON.stringify({ user_ids: userIds }),
    });
  },
  addMembers(groupId: string, userIds: string[]) {
    return request<void>(`/groups/${groupId}/members`, {
      method: "POST",
      body: JSON.stringify({ user_ids: userIds }),
    });
  },
  removeMember(groupId: string, userId: string) {
    return request<void>(`/groups/${groupId}/members/${userId}`, {
      method: "DELETE",
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Notices for administrators
// ─────────────────────────────────────────────────────────────────────────────

/** Something administrators should know now; worded by the console as ``notices.<code>`` with ``params``. */
export interface AdminNotice {
  code: string;
  severity: "info" | "warning" | "error";
  params: Record<string, string | number>;
}

export const adminNotices = {
  /** What the registered sources have to say now (superusers only), errors first. */
  list() {
    return request<AdminNotice[]>("/notices");
  },
  /** Lift a restriction on the tenant nothing enforces (the action of its `restriction_<name>` notice). */
  liftRestriction(name: string) {
    return request<void>(`/notices/restrictions/${encodeURIComponent(name)}`, { method: "DELETE" });
  },
};

/** The restriction a notice is about, for those (`restriction_<name>`) that can be lifted; else null. */
export function liftableRestriction(notice: AdminNotice): string | null {
  return notice.code.startsWith("restriction_") && typeof notice.params.name === "string" ? notice.params.name : null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Auth Providers (SSO)
// ─────────────────────────────────────────────────────────────────────────────

/** A provider people may sign in with (``GET /api/auth/sso/providers``). */
export interface AuthProviderPublic {
  key: string;
  name: string;
  /** ``redirect``: off to the provider and back; ``password``: name and password on this page. */
  kind: "redirect" | "password";
  posts_back?: boolean;
}

export const adminAuthProviders = {
  /** The providers people may sign in with, for the sign-in page (no sign-in needed). */
  async listPublic(): Promise<AuthProviderPublic[]> {
    const res = await fetch("/api/auth/sso/providers", { credentials: "include" });
    if (!res.ok) throw new Error(`sign-in providers: ${res.status}`);
    return (await res.json()) as AuthProviderPublic[];
  },
  /** Where the browser goes to sign in with a redirect provider. */
  startUrl(key: string) {
    return `/api/auth/sso/${encodeURIComponent(key)}/start`;
  },
  /** Sign in with a directory account (a ``password`` provider); the session cookie comes back. */
  async passwordSignIn(key: string, username: string, password: string): Promise<void> {
    await postSignIn(`/api/auth/sso/${encodeURIComponent(key)}/password`, { username, password });
  },
  /**
   * The sign-in that met an existing account and waits, in this browser only (an httpOnly cookie), for the
   * proof that the account is the person's; ``SignInRefused("sign_in_expired")`` when nothing waits.
   */
  async linkWaiting(): Promise<LinkWaiting> {
    const res = await fetch("/api/auth/sso/link", { credentials: "include" });
    if (!res.ok) throw await refusedBy(res);
    return (await res.json()) as LinkWaiting;
  },
  /** Send a code to the account's address (``no_mail``, ``too_soon``, ``too_many`` when not). */
  async linkSendCode(): Promise<void> {
    await postSignIn("/api/auth/sso/link/code", {});
  },
  /** The account's password proves it: the outside sign-in is linked, and the person signed in. */
  async linkWithPassword(password: string): Promise<void> {
    await postSignIn("/api/auth/sso/link/with-password", { password });
  },
  /** The code sent to the account's address proves it: as {@link linkWithPassword}. */
  async linkWithCode(code: string): Promise<void> {
    await postSignIn("/api/auth/sso/link/with-code", { code });
  },
};

/** A sign-in that met an existing account, waiting for the proof (``GET /api/auth/sso/link``). */
export type LinkWaiting = {
  provider_name: string;
  account_email: string;
  expires_at: string;
  /** A code can be sent to the account's address. */
  mail: boolean;
  code_sent: boolean;
};

/**
 * A refused sign-in or link; ``reason`` is the backend's refusal (``wrong_credentials``, ``sign_in_expired``
 * once a link is used or expired, ...), or ``status_<code>`` when it named none.
 */
export class SignInRefused extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}

async function postSignIn(path: string, payload: Record<string, string>): Promise<Response> {
  // Tokens and passwords go in the body, not the address, so they stay out of access logs.
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) return res;
  throw await refusedBy(res);
}

async function refusedBy(res: Response): Promise<SignInRefused> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return new SignInRefused(typeof body.detail === "string" ? body.detail : `status_${res.status}`);
}

/** Fadenstack's own containers: edited only in Root Mode. */
export function isSystemClass(cls: ContainerClass): boolean {
  return cls === "SYSTEM_CORE" || cls === "SYSTEM_AUX";
}

/**
 * A container can be edited (it is replaced) unless its policy is locked;
 * one of Fadenstack's own only in Root Mode.
 */
export function canEdit(
  cls: ContainerClass,
  policy: ContainerPolicy,
  rootModeActive: boolean,
): boolean {
  if (policy === "locked") return false;
  return rootModeActive || !isSystemClass(cls);
}

export function canStop(cls: ContainerClass, rootModeActive: boolean): boolean {
  if (cls === "UNTRUSTED") return false;
  if (cls === "TENANT_APP" || cls === "SYSTEM_AUX" || cls === "MCP")
    return true;
  if (cls === "SYSTEM_CORE") return rootModeActive;
  return false;
}

export function canDelete(
  cls: ContainerClass,
  rootModeActive: boolean,
): boolean {
  if (cls === "UNTRUSTED") return false;
  if (cls === "TENANT_APP") return true;
  return rootModeActive;
}

export function canPause(
  cls: ContainerClass,
  rootModeActive: boolean,
): boolean {
  if (cls === "UNTRUSTED" || cls === "MCP") return false;
  if (cls === "TENANT_APP") return true;
  return rootModeActive;
}

export function canLogs(
  cls: ContainerClass,
  _rootModeActive: boolean,
): boolean {
  // All container classes can view logs
  return true;
}

export const CLASS_COLORS: Record<ContainerClass, string> = {
  SYSTEM_CORE: "bg-red-100 text-red-800 border-red-200",
  SYSTEM_AUX: "bg-amber-100 text-amber-800 border-amber-200",
  MCP: "bg-purple-100 text-purple-800 border-purple-200",
  TENANT_APP: "bg-green-100 text-green-800 border-green-200",
  UNTRUSTED: "bg-gray-100 text-gray-700 border-gray-200",
};

// ─────────────────────────────────────────────────────────────────────────────
// Hardware detection
// ─────────────────────────────────────────────────────────────────────────────

export interface GpuDevice {
  index: number;
  vendor: string;
  model: string;
  vram_bytes: number;
  driver_version: string;
  compute_api: string;
}

export interface GpuInventory {
  devices: GpuDevice[];
  primary_vendor: string;
  primary_compute_api: string;
  has_gpu: boolean;
  device_count: number;
  total_vram_bytes: number;
}

export interface GpuMetrics {
  util_percent: number | null;
  vram_used_bytes: number | null;
  vram_total_bytes: number | null;
}

export interface VllmImagePreset {
  label: string;
  image: string;
  vendor: string | null;
  description: string | null;
  is_default: boolean;
  is_recommended: boolean;
}

export interface HardwareInfo {
  gpu: GpuInventory;
  gpu_metrics: GpuMetrics;
  recommended_vllm_image: string | null;
  legacy_vllm_image: string | null;
  vllm_image_presets: VllmImagePreset[];
}

export const hardware = {
  info() {
    return request<HardwareInfo>("/hardware");
  },
  rescan() {
    return request<HardwareInfo>("/hardware/rescan", { method: "POST" });
  },
};
