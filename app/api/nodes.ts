import type { HardwareInfo } from "~/api/admin";

const BASE = "/api/admin/system";

export interface NodeEnrollmentToken {
  id: string;
  token: string;
  expires_at: string;
  note?: string | null;
}

export interface ManagedNode {
  id: string;
  agent_id: string;
  host: string;
  status: string;
  version?: string | null;
  labels: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  maintenance_mode: boolean;
  draining: boolean;
  scheduler_eligible: boolean;
  last_seen?: string | null;
  created_at: string;
  updated_at: string;
  latest_inventory?: Record<string, unknown> | null;
  latest_utilization?: Record<string, unknown> | null;
}

export interface NodeCommand {
  id: string;
  node_id: string;
  command_type: string;
  status: string;
  correlation_id?: string | null;
  idempotency_key: string;
  payload: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  timeout_sec?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  issued_at: string;
  dispatched_at?: string | null;
  acked_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface NodeCommandTimeline {
  command: NodeCommand;
  events: Array<{
    seq: number;
    phase: string;
    message: string;
    payload?: Record<string, unknown> | null;
    ts: string;
  }>;
}

interface IssueNodeCommandPayload {
  command_type: string;
  payload?: Record<string, unknown>;
  idempotency_key?: string;
  correlation_id?: string;
  timeout_sec?: number;
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
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const nodesApi = {
  createEnrollmentToken(note?: string) {
    return request<NodeEnrollmentToken>("/nodes/enrollment-tokens", {
      method: "POST",
      body: JSON.stringify({ note: note?.trim() || null }),
    });
  },

  list() {
    return request<ManagedNode[]>("/nodes");
  },

  get(nodeId: string) {
    return request<ManagedNode>(`/nodes/${encodeURIComponent(nodeId)}`);
  },

  delete(nodeId: string) {
    return request<void>(`/nodes/${encodeURIComponent(nodeId)}`, {
      method: "DELETE",
    });
  },

  setMaintenance(nodeId: string, enabled: boolean, reason?: string) {
    return request<ManagedNode>(
      `/nodes/${encodeURIComponent(nodeId)}/maintenance`,
      {
        method: "POST",
        body: JSON.stringify({
          enabled,
          reason: reason?.trim() || null,
        }),
      },
    );
  },

  setDrain(nodeId: string, enabled: boolean) {
    return request<ManagedNode>(`/nodes/${encodeURIComponent(nodeId)}/drain`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  },

  issueCommand(nodeId: string, payload: IssueNodeCommandPayload) {
    return request<NodeCommand>(
      `/nodes/${encodeURIComponent(nodeId)}/commands`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },

  listCommands(nodeId: string) {
    return request<NodeCommand[]>(
      `/nodes/${encodeURIComponent(nodeId)}/commands`,
    );
  },

  commandTimeline(nodeId: string, commandId: string) {
    return request<NodeCommandTimeline>(
      `/nodes/${encodeURIComponent(nodeId)}/commands/${encodeURIComponent(commandId)}`,
    );
  },

  /** Get GPU hardware info for a specific node (same shape as /admin/hardware). */
  hardware(nodeId: string) {
    return request<HardwareInfo>(
      `/nodes/${encodeURIComponent(nodeId)}/hardware`,
    );
  },
};
