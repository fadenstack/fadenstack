/**
 * Model marketplace API: Hugging Face models, their fit on each cluster, hosting.
 *
 * Every list answers with `hub`: "online", or "offline" when this server
 * cannot reach Hugging Face -- a normal installation, shown as such.
 */
import type { EngineConfig } from "~/lib/engine";

const BASE = "/api/llm/marketplace";

export type HubState = "online" | "offline";
export type FitStatus = "fits" | "too_large" | "unknown" | "no_accelerators";
export type ModelTask = "chat" | "embedding" | "vision" | "images" | "other";
export type MarketSort = "trending" | "downloads" | "likes" | "recent";

/** A Mac's memory plan (CONTRACTS § C3), present when the cluster is one Mac. */
export interface MetalPlan {
  working_set_bytes: number;
  unified_bytes?: number;
  estimated: boolean;
  reserve_bytes?: number;
  ceiling_bytes?: number;
  budget_bytes?: number;
  kv_bytes?: number;
  profile?: string;
  /** The Mac runtime's own version (vLLM-Metal), not the Linux engine's. */
  runtime_version?: string | null;
}

/** What the form may offer for a Mac (the backend's `metal_policy.form_policy`). */
export interface MetalPolicy {
  /** Engine settings a Mac accepts; every other one is hidden or disabled. */
  allowed_keys: string[];
  /** Settings whose only value is fixed (prefix caching stays off). */
  forced: Record<string, unknown>;
  tool_parsers: string[];
  reasoning_parsers: string[];
}

export interface Fit {
  status: FitStatus;
  max_num_seqs?: number | null;
  metal?: MetalPlan | null;
  /** Accelerators one copy spans, or a fraction of one when it shares a card. */
  gpus_per_copy: number | null;
  tensor_parallel: number | null;
  copies: number;
  copies_now: number;
  context: number | null;
  max_context: number | null;
  needed_bytes_per_gpu: number | null;
  gpu_bytes: number | null;
  weights_bytes: number | null;
  kv_bytes_per_token: number | null;
  suggested_gpu_memory_utilization: number | null;
  shareable: boolean;
  notes: string[];
}

export interface CuratedInfo {
  group: string;
  /** Translation key for why it is on the list. */
  blurb: string;
  verified: { on: string; date: string } | null;
}

export interface LocalCopy {
  model_id: string;
  status: "available" | "downloading" | "failed" | string;
  deployments: number;
  /** Whether the files are on this server: a catalogue entry alone is not a copy. */
  on_server: boolean;
}

export interface MarketModel {
  repo_id: string;
  name: string;
  author: string | null;
  downloads?: number;
  likes?: number;
  trending_score?: number | null;
  created_at?: string | null;
  last_modified?: string | null;
  pipeline_tag?: string | null;
  license?: string | null;
  gated?: boolean;
  params_b: number | null;
  active_params_b?: number | null;
  weights_bytes: number | null;
  dtype?: string | null;
  format: string;
  quantization?: string | null;
  capabilities: string[];
  task: ModelTask;
  runnable: boolean;
  not_runnable_reason?: string | null;
  architecture?: string | null;
  /** A picture model's diffusers pipeline (``ZImagePipeline``). */
  pipeline?: string | null;
  base_model?: string | null;
  curated?: CuratedInfo;
  fit?: Fit | null;
  local?: LocalCopy | null;
}

export interface MarketModelDetail extends MarketModel {
  summary?: string | null;
  max_context?: number | null;
  kv_bytes_per_token?: number | null;
  needs_remote_code?: boolean;
  files?: { name: string; size: number | null }[];
  total_bytes?: number | null;
  architecture_facts?: Record<string, unknown>;
}

export interface MarketGpu {
  name: string;
  total_bytes: number;
  free_bytes: number | null;
}

export interface MarketCluster {
  environment_id: string;
  name: string;
  status: string;
  machines: { node_id: string; name: string; gpus: MarketGpu[] }[];
  vllm_version: string | null;
  gpu_count: number;
  gpu_bytes: number | null;
  accelerator: string | null;
  /** Set for a standalone (Mac) cluster. */
  metal?: Partial<MetalPlan> | null;
}

export interface MarketList {
  hub: HubState;
  cluster_id: string | null;
  items: MarketModel[];
  groups?: string[];
}

/** vLLM's own recipe for a model (recipes.vllm.ai), as it applies here. */
export interface VllmRecipe {
  hf_id: string;
  url: string;
  title: string | null;
  min_vllm_version: string | null;
  context_length: number | null;
  /** The settings it turns on by default, already in the suggestions. */
  config: EngineConfig;
  /** Arguments it names that cannot be used on this server, as written. */
  dropped: string[];
  opt_in: { name: string; description: string; args: string[]; usable: boolean; config: EngineConfig }[];
  hardware: string | null;
  runtime_too_old: boolean;
}

/** Whether a cluster's runtime can load the model at all (``services/marketplace/compat.py``). */
export interface Compatibility {
  status: "runs" | "may_not_run" | "cannot_run" | "unknown";
  code:
    | "model_type_unknown"
    | "architecture_not_native"
    | "newer_transformers"
    | "remote_code"
    | "no_config"
    | "no_image_app"
    | "pipeline_unknown"
    | null;
  message: string | null;
  model_type: string | null;
  architectures: string[];
  model_transformers_version: string | null;
  vllm_version: string | null;
  transformers_version: string | null;
}

export interface MarketDetail {
  hub: HubState;
  model: MarketModelDetail;
  clusters: MarketCluster[];
  cluster_id: string | null;
  fits: Record<string, Fit>;
  /** Per cluster; absent from an older backend. */
  compatibility?: Record<string, Compatibility>;
  suggested: { config: EngineConfig; reasons: Record<string, string> };
  /** Set when the chosen cluster is one Mac. */
  metal_policy?: MetalPolicy | null;
  recipe?: VllmRecipe | null;
  local: LocalCopy | null;
}

export interface HostPayload {
  repo_id: string;
  environment_id: string;
  name: string;
  alias: string | null;
  copies: number;
  gpus_per_copy: number;
  engine_config: EngineConfig;
  revision?: string | null;
  /** The cluster machine every copy runs on; omitted: Ray places them. */
  machine?: string | null;
}

export interface ImageGeneration {
  enabled: boolean;
  /** "images_disabled" while it is off. */
  code?: string;
}

/** The error code of a refused request (`images_disabled`), when the server sent one. */
export function errorCode(err: unknown): string | undefined {
  return err && typeof err === "object" && "code" in err ? String((err as { code: unknown }).code) : undefined;
}

export interface HostResult {
  deployment_id: string;
  model_id: string;
  download: "kept" | "started" | "none";
  download_error: string | null;
}

/** A download as the worker last recorded it; the page only reads it. */
export interface KeptDownload {
  job_id: string;
  status: "queued" | "running" | "success" | "failed" | "canceled";
  progress: number;
  error: string | null;
  updated_at: string | null;
}

/** A model this server keeps, with what the "On this server" view needs. */
export interface KeptModel {
  model_id: string;
  display_name: string;
  hf_repo_id: string | null;
  hf_revision: string | null;
  source: "huggingface" | "local_path" | "archive_import" | "remote" | string;
  status: "available" | "downloading" | "failed" | "deleting" | string;
  created_at: string | null;
  size_bytes: number | null;
  download: KeptDownload | null;
  deployments: { id: string; name: string; cluster: string | null; phase: string; desired_state: string }[];
  runtimes: { id: string; name: string; status: string }[];
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    let message = text;
    let code: string | undefined;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") message = parsed.detail;
      else if (Array.isArray(parsed.detail)) {
        message = parsed.detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ");
      } else if (parsed.detail && typeof parsed.detail === "object") {
        // A refusal with a machine-readable code, e.g. {"code": "images_disabled", "message": "..."}.
        const d = parsed.detail as { code?: unknown; message?: unknown };
        if (typeof d.message === "string") message = d.message;
        if (typeof d.code === "string") code = d.code;
      }
    } catch {
      // not JSON
    }
    const error = new Error(message) as Error & { status?: number; code?: string };
    error.status = res.status;
    if (code) error.code = code;
    throw error;
  }
  return res.json() as Promise<T>;
}

/** What the Hub accepts as an organisation or user name (the server checks the same). */
export const AUTHOR_PATTERN = /^[A-Za-z0-9][\w.-]{0,95}$/;

/** The owner's picture on the Hub, fetched and kept by the server; 404 when there is none. */
export function avatarUrl(owner: string): string {
  return `${BASE}/avatars/${encodeURIComponent(owner)}`;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const marketplaceApi = {
  clusters() {
    return request<MarketCluster[]>("/clusters");
  },
  recommended(clusterId?: string | null) {
    return request<MarketList>(`/recommended${query({ cluster_id: clusterId })}`);
  },
  /** *q* may hold `*` (any text) and `?` (one character); *author* is one Hub organisation or user. */
  search(params: {
    q?: string;
    sort?: MarketSort;
    task?: ModelTask;
    clusterId?: string | null;
    limit?: number;
    author?: string | null;
  }) {
    return request<MarketList>(
      `/search${query({
        q: params.q,
        sort: params.sort,
        task: params.task,
        cluster_id: params.clusterId,
        limit: params.limit,
        author: params.author,
      })}`,
    );
  },
  kept() {
    return request<{ items: KeptModel[] }>("/kept");
  },
  /** `sequences` and `dtype` only re-plan a Mac (its share follows them); other clusters ignore them. */
  detail(
    repoId: string,
    clusterId?: string | null,
    context?: number | null,
    macPlan?: { sequences?: number | null; dtype?: string | null },
  ) {
    return request<MarketDetail>(
      `/models/${repoId}${query({ cluster_id: clusterId, context, sequences: macPlan?.sequences, dtype: macPlan?.dtype })}`,
    );
  },
  /** Whether this install may generate pictures. The one source of truth for the console. */
  imageGeneration() {
    return request<ImageGeneration>("/image-generation");
  },
  host(payload: HostPayload) {
    return request<HostResult>("/host", { method: "POST", body: JSON.stringify(payload) });
  },
};
