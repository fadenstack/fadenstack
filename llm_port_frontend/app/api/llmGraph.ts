/**
 * LLM Graph API client.
 */

const BASE = "/api/llm/graph";

export type GraphNodeType = "provider" | "runtime" | "model" | "trace";

export interface GraphNode {
  id: string;
  type: GraphNodeType | string;
  label: string;
  status: string | null;
  meta: Record<string, unknown> | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface TopologyResponse {
  generated_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface TraceEvent {
  event_id: number;
  ts: string;
  request_id: string;
  trace_id: string | null;
  tenant_id: string;
  user_id: string;
  model_alias: string | null;
  provider_instance_id: string | null;
  status: number;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
}

export interface TraceSnapshotResponse {
  items: TraceEvent[];
  /**
   * Where to carry on from. A string on purpose: an event id is about 1.8e21,
   * past what a JavaScript number holds exactly, so ``event_id`` itself comes
   * back rounded and sent as ``1.79e+21``, which the server refuses.
   */
  next_cursor: string | null;
}

export interface DataUsagePerInstance {
  provider_instance_id: string;
  /** The provider it serves: a cluster or found container has no runtime to go by. */
  provider_id?: string | null;
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  error_count: number;
}

export interface DataUsageSummary {
  generated_at: string;
  instances: DataUsagePerInstance[];
  grand_total_requests: number;
  grand_total_tokens: number;
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function getLlmGraphTopology(): Promise<TopologyResponse> {
  return request<TopologyResponse>("/topology");
}

export function getLlmGraphTraces(
  limit = 100,
  afterEventId?: string,
): Promise<TraceSnapshotResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (afterEventId !== undefined) {
    params.set("after_event_id", afterEventId);
  }
  return request<TraceSnapshotResponse>(`/traces?${params.toString()}`);
}

export function getLlmDataUsage(): Promise<DataUsageSummary> {
  return request<DataUsageSummary>("/data-usage");
}

/**
 * Whether this server streams traces. Without the Enterprise plugin the stream
 * answers 402, which an EventSource cannot see: it only reports an error.
 */
export async function traceStreamAvailable(): Promise<boolean> {
  const controller = new AbortController();
  try {
    const res = await fetch(`${BASE}/traces/stream`, { credentials: "include", signal: controller.signal });
    return res.status !== 402;
  } catch {
    return true;
  } finally {
    // An open stream is only a yes; it is not read here.
    controller.abort();
  }
}

export function openLlmGraphTraceStream(
  onEvent: (event: TraceEvent, cursor: string) => void,
  onError: (error: Event) => void,
  cursor?: string,
): EventSource {
  const params = new URLSearchParams();
  if (cursor !== undefined) {
    params.set("cursor", cursor);
  }
  const url = `${BASE}/traces/stream${params.toString() ? `?${params.toString()}` : ""}`;
  const source = new EventSource(url, { withCredentials: true });
  source.addEventListener("trace", (raw) => {
    const message = raw as MessageEvent<string>;
    const data = JSON.parse(message.data) as TraceEvent;
    // The event's ``id:`` line is its id exactly; ``data.event_id`` is rounded.
    onEvent(data, message.lastEventId || String(data.event_id));
  });
  source.addEventListener("ping", () => {
    // keep-alive event
  });
  source.onerror = onError;
  return source;
}
