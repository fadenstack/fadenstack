/**
 * API client for the Chat & Sessions admin endpoints.
 *
 * Mirrors the backend routes at /api/admin/chat/*.
 */

const BASE = "/api/admin/chat";

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
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Types ────────────────────────────────────────────────────────

export interface ChatProject {
  id: string;
  tenant_id: string;
  user_id: string;
  name: string;
  description: string | null;
  model_alias: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  user_id: string;
  project_id: string | null;
  title: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ChatAttachment {
  id: string;
  tenant_id: string;
  user_id: string;
  session_id: string | null;
  project_id: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  extraction_status: string;
  scope: string;
  page_count: number | null;
  truncated: boolean;
  created_at: string;
}

export interface ChatStats {
  total_projects: number;
  total_sessions: number;
  total_attachments: number;
  total_attachment_bytes: number;
}

// ── API ──────────────────────────────────────────────────────────

export const chatAdmin = {
  listProjects: () =>
    request<{ data: ChatProject[] }>("/projects").then((r) => r.data),

  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),

  listSessions: (projectId?: string) => {
    const qs = projectId ? `?project_id=${projectId}` : "";
    return request<{ data: ChatSession[] }>(`/sessions${qs}`).then(
      (r) => r.data,
    );
  },

  deleteSession: (id: string) =>
    request<void>(`/sessions/${id}`, { method: "DELETE" }),

  listAttachments: (params?: { session_id?: string; project_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.session_id) qs.set("session_id", params.session_id);
    if (params?.project_id) qs.set("project_id", params.project_id);
    const q = qs.toString();
    return request<{ data: ChatAttachment[] }>(
      `/attachments${q ? `?${q}` : ""}`,
    ).then((r) => r.data);
  },

  deleteAttachment: (id: string) =>
    request<void>(`/attachments/${id}`, { method: "DELETE" }),

  stats: () => request<ChatStats>("/stats"),
};
