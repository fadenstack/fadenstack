import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type Edge,
  type Node,
  type NodeMouseHandler,
  MarkerType,
} from "reactflow";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import {
  getLlmGraphTopology,
  getLlmGraphTraces,
  openLlmGraphTraceStream,
  type GraphEdge,
  type GraphNode,
  type TraceEvent,
} from "~/api/llmGraph";

const MAX_TRACE_NODES = 300;
const MAX_TRACE_EDGES = 600;
const TRACE_FETCH_LIMIT = 100;
const FLUSH_INTERVAL_MS = 150;

type GraphMode = "topology" | "live";

interface SelectedNode {
  id: string;
  label: string;
  type: string;
  status?: string;
  meta?: Record<string, unknown>;
}

export default function GraphPage() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<GraphMode>("topology");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [streamPaused, setStreamPaused] = useState(false);
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(null);
  const [baseNodes, setBaseNodes] = useState<Node[]>([]);
  const [baseEdges, setBaseEdges] = useState<Edge[]>([]);
  const [traceNodes, setTraceNodes] = useState<Node[]>([]);
  const [traceEdges, setTraceEdges] = useState<Edge[]>([]);

  const queueRef = useRef<TraceEvent[]>([]);
  const seenEventIdsRef = useRef<Set<number>>(new Set());
  const lastEventIdRef = useRef<number | undefined>(undefined);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);

  const toFlowNode = useCallback((node: GraphNode, index: number): Node => {
    const groupWidth = 340;
    const rowHeight = 120;
    const groupOrder: Record<string, number> = { provider: 0, runtime: 1, model: 2, trace: 3 };
    const col = groupOrder[node.type] ?? 3;
    const row = Math.floor(index / 3);
    return {
      id: node.id,
      type: "default",
      position: { x: col * groupWidth, y: row * rowHeight },
      data: {
        label: node.label,
        type: node.type,
        status: node.status,
        meta: node.meta ?? {},
      },
      draggable: false,
      selectable: true,
      style: nodeStyle(node.type, node.status ?? undefined),
    };
  }, []);

  const toFlowEdge = useCallback((edge: GraphEdge): Edge => {
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
      },
      style: {
        strokeWidth: 1.2,
      },
      animated: edge.type === "trace_link",
    };
  }, []);

  const loadTopology = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const topology = await getLlmGraphTopology();
      setBaseNodes(topology.nodes.map(toFlowNode));
      setBaseEdges(topology.edges.map(toFlowEdge));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_graph.failed_load"));
    } finally {
      setLoading(false);
    }
  }, [t, toFlowEdge, toFlowNode]);

  const seedTraces = useCallback(async () => {
    try {
      const snapshot = await getLlmGraphTraces(TRACE_FETCH_LIMIT);
      for (const item of snapshot.items) {
        queueRef.current.push(item);
      }
      if (snapshot.items.length > 0) {
        lastEventIdRef.current = snapshot.items[snapshot.items.length - 1].event_id;
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_graph.failed_load_traces"));
    }
  }, [t]);

  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connectStream = useCallback(() => {
    if (streamPaused || mode !== "live") {
      return;
    }
    closeStream();
    const source = openLlmGraphTraceStream(
      (event) => {
        reconnectAttemptRef.current = 0;
        queueRef.current.push(event);
        lastEventIdRef.current = event.event_id;
      },
      () => {
        closeStream();
        reconnectAttemptRef.current += 1;
        const delayMs = Math.min(10_000, 1000 * 2 ** reconnectAttemptRef.current);
        reconnectTimerRef.current = window.setTimeout(() => {
          connectStream();
        }, delayMs);
      },
      lastEventIdRef.current,
    );
    eventSourceRef.current = source;
  }, [closeStream, mode, streamPaused]);

  useEffect(() => {
    void loadTopology();
  }, [loadTopology]);

  useEffect(() => {
    if (mode !== "live") {
      closeStream();
      return;
    }
    void seedTraces().then(() => {
      connectStream();
    });
    return () => {
      closeStream();
    };
  }, [closeStream, connectStream, mode, seedTraces]);

  useEffect(() => {
    const handle = window.setInterval(() => {
      if (queueRef.current.length === 0) {
        return;
      }
      const batch = queueRef.current.splice(0, queueRef.current.length);
      applyTraceBatch(batch, setTraceNodes, setTraceEdges, seenEventIdsRef, baseNodes);
    }, FLUSH_INTERVAL_MS);
    return () => {
      window.clearInterval(handle);
    };
  }, [baseNodes]);

  useEffect(() => {
    return () => {
      closeStream();
    };
  }, [closeStream]);

  const visibleNodes = useMemo(() => {
    const all = mode === "live" ? [...baseNodes, ...traceNodes] : baseNodes;
    const needle = search.trim().toLowerCase();
    return all.filter((node) => {
      const label = String((node.data as { label?: string })?.label ?? "").toLowerCase();
      const status = String((node.data as { status?: string })?.status ?? "");
      const statusOk = statusFilter === "all" || status === statusFilter;
      const searchOk = needle.length === 0 || label.includes(needle) || node.id.toLowerCase().includes(needle);
      return statusOk && searchOk;
    });
  }, [baseNodes, mode, search, statusFilter, traceNodes]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  const visibleEdges = useMemo(() => {
    const all = mode === "live" ? [...baseEdges, ...traceEdges] : baseEdges;
    return all.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target));
  }, [baseEdges, baseEdges.length, mode, traceEdges, visibleNodeIds]);

  const onNodeClick: NodeMouseHandler = useCallback((_evt, node) => {
    const data = (node.data ?? {}) as {
      label?: string;
      type?: string;
      status?: string;
      meta?: Record<string, unknown>;
    };
    setSelectedNode({
      id: node.id,
      label: data.label ?? node.id,
      type: data.type ?? "unknown",
      status: data.status,
      meta: data.meta,
    });
  }, []);

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 2, height: "100%", minHeight: 0 }}>
      <Stack spacing={1} sx={{ minWidth: 0, minHeight: 0, height: "100%" }}>
        <Paper sx={{ p: 1.5, display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            size="small"
            value={mode}
            exclusive
            onChange={(_e, value: GraphMode | null) => {
              if (value) setMode(value);
            }}
          >
            <ToggleButton value="topology">{t("llm_graph.topology_mode")}</ToggleButton>
            <ToggleButton value="live">{t("llm_graph.live_mode")}</ToggleButton>
          </ToggleButtonGroup>
          <TextField
            size="small"
            label={t("llm_graph.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <FormControl size="small" sx={{ minWidth: 170 }}>
            <InputLabel>{t("llm_graph.status_filter")}</InputLabel>
            <Select
              label={t("llm_graph.status_filter")}
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <MenuItem value="all">{t("table.all")}</MenuItem>
              <MenuItem value="running">running</MenuItem>
              <MenuItem value="stopped">stopped</MenuItem>
              <MenuItem value="error">error</MenuItem>
              <MenuItem value="available">available</MenuItem>
            </Select>
          </FormControl>
          {mode === "live" && (
            <Button size="small" variant="outlined" onClick={() => setStreamPaused((prev) => !prev)}>
              {streamPaused ? t("llm_graph.resume_stream") : t("llm_graph.pause_stream")}
            </Button>
          )}
          <Button size="small" onClick={() => void loadTopology()}>
            {t("dashboard.refresh")}
          </Button>
          <Chip size="small" label={`${t("llm_graph.nodes")}: ${visibleNodes.length}`} />
          <Chip size="small" label={`${t("llm_graph.edges")}: ${visibleEdges.length}`} />
        </Paper>
        {error && <Alert severity="error">{error}</Alert>}
        <Paper sx={{ flex: 1, minHeight: 0 }}>
          {loading ? (
            <Box sx={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Typography color="text.secondary">{t("common.loading")}</Typography>
            </Box>
          ) : (
            <ReactFlow
              fitView
              nodes={visibleNodes}
              edges={visibleEdges}
              onNodeClick={onNodeClick}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              panOnDrag
              zoomOnScroll
            >
              <MiniMap pannable zoomable />
              <Controls />
              <Background />
            </ReactFlow>
          )}
        </Paper>
      </Stack>
      <Paper sx={{ p: 2, overflow: "auto", height: "100%", minHeight: 0 }}>
        <Typography variant="h6">{t("llm_graph.node_details")}</Typography>
        {!selectedNode ? (
          <Typography variant="body2" color="text.secondary">
            {t("llm_graph.select_node")}
          </Typography>
        ) : (
          <Stack spacing={1} sx={{ mt: 1 }}>
            <Typography variant="body2"><strong>ID:</strong> {selectedNode.id}</Typography>
            <Typography variant="body2"><strong>{t("common.name")}:</strong> {selectedNode.label}</Typography>
            <Typography variant="body2"><strong>{t("llm_graph.type")}:</strong> {selectedNode.type}</Typography>
            {selectedNode.status && (
              <Typography variant="body2"><strong>{t("common.status")}:</strong> {selectedNode.status}</Typography>
            )}
            <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: "0.78rem" }}>
              {JSON.stringify(selectedNode.meta ?? {}, null, 2)}
            </Typography>
          </Stack>
        )}
      </Paper>
    </Box>
  );
}

function nodeStyle(type: string, status?: string): Record<string, string | number> {
  const palette = {
    provider: "#0D47A1",
    runtime: "#1B5E20",
    model: "#4A148C",
    trace: "#BF360C",
    default: "#37474F",
  };
  const border = status === "error" ? "#D32F2F" : palette[type as keyof typeof palette] ?? palette.default;
  return {
    border: `2px solid ${border}`,
    borderRadius: 10,
    fontSize: 12,
    padding: 8,
    minWidth: 140,
    background: "#fff",
  };
}

function applyTraceBatch(
  batch: TraceEvent[],
  setTraceNodes: React.Dispatch<React.SetStateAction<Node[]>>,
  setTraceEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
  seenEventIdsRef: React.MutableRefObject<Set<number>>,
  baseNodes: Node[],
): void {
  if (batch.length === 0) return;
  const existingIds = new Set(baseNodes.map((n) => n.id));
  const newEventIds = new Set<number>();
  setTraceNodes((prev) => {
    const next = [...prev];
    for (const event of batch) {
      if (seenEventIdsRef.current.has(event.event_id)) {
        continue;
      }
      seenEventIdsRef.current.add(event.event_id);
      newEventIds.add(event.event_id);
      next.push(buildTraceNode(event, next.length));
    }
    if (next.length > MAX_TRACE_NODES) {
      const toDrop = next.length - MAX_TRACE_NODES;
      next.splice(0, toDrop);
    }
    return next;
  });

  setTraceEdges((prev) => {
    const next = [...prev];
    for (const event of batch) {
      if (!newEventIds.has(event.event_id)) {
        continue;
      }
      const traceId = `trace:${event.event_id}`;
      const anchor = resolveTraceAnchor(event, existingIds);
      next.push({
        id: `trace-edge:${event.event_id}`,
        source: anchor,
        target: traceId,
        type: "smoothstep",
        animated: true,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        style: { stroke: "#EF6C00" },
      });
    }
    if (next.length > MAX_TRACE_EDGES) {
      const toDrop = next.length - MAX_TRACE_EDGES;
      next.splice(0, toDrop);
    }
    return next;
  });
}

function resolveTraceAnchor(event: TraceEvent, existingIds: Set<string>): string {
  if (event.provider_instance_id) {
    const providerNodeId = `provider:${event.provider_instance_id}`;
    if (existingIds.has(providerNodeId)) {
      return providerNodeId;
    }
  }
  const runtime = Array.from(existingIds).find((id) => id.startsWith("runtime:"));
  if (runtime) return runtime;
  const provider = Array.from(existingIds).find((id) => id.startsWith("provider:"));
  return provider ?? "trace:root";
}

function buildTraceNode(event: TraceEvent, index: number): Node {
  return {
    id: `trace:${event.event_id}`,
    position: {
      x: 1020 + (index % 4) * 28,
      y: 50 + (index % 25) * 24,
    },
    data: {
      label: `${event.status} · ${event.latency_ms}ms`,
      type: "trace",
      status: event.status >= 400 ? "error" : "ok",
      meta: {
        request_id: event.request_id,
        trace_id: event.trace_id,
        tenant_id: event.tenant_id,
        model_alias: event.model_alias,
        provider_instance_id: event.provider_instance_id,
        prompt_tokens: event.prompt_tokens,
        completion_tokens: event.completion_tokens,
        total_tokens: event.total_tokens,
        error_code: event.error_code,
        ts: event.ts,
      },
    },
    style: nodeStyle("trace", event.status >= 400 ? "error" : "ok"),
    draggable: false,
    selectable: true,
  };
}
