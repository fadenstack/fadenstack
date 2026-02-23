/**
 * Admin RAG API client — typed wrappers for /api/admin/rag endpoints.
 */

const BASE = "/api/admin/rag";

export type RagEmbeddingProvider = "openai_compat" | "local" | "huggingface";
export type RagSearchMode = "vector" | "keyword" | "hybrid";

export interface RagChunkingPolicy {
  max_tokens: number;
  overlap: number;
  by_headings: boolean;
}

export interface RagRuntimeConfigPayload {
  embedding_provider: RagEmbeddingProvider;
  embedding_model: string;
  embedding_base_url: string | null;
  embedding_api_key_ref: string | null;
  embedding_dim: number;
  chunking_policy: RagChunkingPolicy;
}

export interface RagRuntimeConfigResponse {
  updated_at: string;
  payload: RagRuntimeConfigPayload;
}

export interface RagRuntimeConfigUpdateRequest {
  payload: RagRuntimeConfigPayload;
  embedding_api_key?: string | null;
}

export interface RagSearchPrincipals {
  user_id: string;
  group_ids: string[];
}

export interface RagSearchFilters {
  sources: string[];
  tags: string[];
  doc_types: string[];
  time_from: string | null;
  time_to: string | null;
}

export interface RagKnowledgeSearchRequest {
  tenant_id: string;
  workspace_id: string | null;
  query: string;
  principals: RagSearchPrincipals;
  filters: RagSearchFilters;
  top_k: number;
  mode: RagSearchMode;
  debug: boolean;
}

export interface RagSearchResult {
  chunk_text: string;
  doc_title: string | null;
  source_uri: string;
  section: string | null;
  score: number;
  metadata: Record<string, unknown>;
}

export interface RagKnowledgeSearchResponse {
  results: RagSearchResult[];
  debug: Record<string, unknown> | null;
}

export interface RagCollectorSummary {
  id: string;
  type: string;
  enabled: boolean;
  schedule: string;
  tenant_id: string;
  workspace_id: string | null;
  config: Record<string, unknown>;
}

export interface RagCollectorListResponse {
  collectors: RagCollectorSummary[];
}

export interface RagRunCollectorResponse {
  job_id: string;
  source_id: string;
  status: string;
}

export interface RagIngestJobEvent {
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RagIngestJob {
  job_id: string;
  collector_id: string;
  source_id: string | null;
  tenant_id: string;
  workspace_id: string | null;
  status: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  events: RagIngestJobEvent[];
}

export interface RagJobListResponse {
  jobs: RagIngestJob[];
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

export const ragRuntime = {
  health() {
    return request<{ status: string }>("/health");
  },
  getConfig() {
    return request<RagRuntimeConfigResponse>("/runtime-config");
  },
  updateConfig(body: RagRuntimeConfigUpdateRequest) {
    return request<RagRuntimeConfigResponse>("/runtime-config", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragKnowledge = {
  search(body: RagKnowledgeSearchRequest) {
    return request<RagKnowledgeSearchResponse>("/knowledge/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragCollectors = {
  list() {
    return request<RagCollectorListResponse>("/collectors");
  },
  run(collectorId: string) {
    return request<RagRunCollectorResponse>(`/collectors/${collectorId}/run`, {
      method: "POST",
    });
  },
};

export const ragJobs = {
  list(limit = 50) {
    return request<RagJobListResponse>(`/jobs?limit=${limit}`);
  },
  get(jobId: string) {
    return request<RagIngestJob>(`/jobs/${jobId}`);
  },
};

