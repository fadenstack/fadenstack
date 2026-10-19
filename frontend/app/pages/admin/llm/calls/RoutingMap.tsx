import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";

import Box from "@mui/material/Box";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";

import type { LlmCall, LlmRoute } from "~/api/llmCalls";

import { formatAgo, formatCount, formatDuration, formatPercent, shortId } from "./format";

interface Props {
  routes: LlmRoute[];
  /** The call open in the list: its way through is drawn out. */
  selected: LlmCall | null;
  onFilterModel: (model: string) => void;
  now: number;
}

interface FlowNodeData {
  kind: "gateway" | "model" | "provider";
  title: string;
  subtitle: string;
  calls: number;
  errors: number;
  highlighted: boolean;
  dimmed: boolean;
}

function providerKey(r: Pick<LlmRoute, "provider_id" | "provider_instance_id">): string {
  if (r.provider_id) return `provider:${r.provider_id}`;
  if (r.provider_instance_id) return `instance:${r.provider_instance_id}`;
  return "unrouted";
}

function errorTone(rate: number): "ok" | "warn" | "bad" {
  if (rate >= 0.1) return "bad";
  if (rate >= 0.02) return "warn";
  return "ok";
}

const FlowNode = memo(({ data }: NodeProps<FlowNodeData>) => {
  const theme = useTheme();
  const { t } = useTranslation();
  const rate = data.calls ? data.errors / data.calls : 0;
  const tone = errorTone(rate);
  const accent =
    data.kind === "gateway"
      ? theme.palette.primary.main
      : tone === "bad"
        ? theme.palette.error.main
        : tone === "warn"
          ? theme.palette.warning.main
          : data.kind === "model"
            ? theme.palette.info.main
            : theme.palette.success.main;
  return (
    <Box
      sx={{
        width: 210,
        px: 1.25,
        py: 1,
        borderRadius: 1.5,
        border: 1.5,
        borderColor: data.highlighted ? theme.palette.primary.main : alpha(accent, 0.6),
        borderLeft: `4px solid ${accent}`,
        bgcolor: "background.paper",
        boxShadow: data.highlighted ? `0 0 0 3px ${alpha(theme.palette.primary.main, 0.3)}` : 1,
        opacity: data.dimmed ? 0.35 : 1,
        cursor: data.kind === "model" ? "pointer" : "default",
        transition: "opacity 0.2s",
      }}
    >
      {data.kind !== "gateway" && <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />}
      <Typography variant="overline" sx={{ lineHeight: 1.2, color: "text.secondary", fontSize: "0.62rem" }}>
        {t(`llm_calls.node_${data.kind}`)}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap title={data.title}>
        {data.title}
      </Typography>
      <Typography variant="caption" color="text.secondary" component="div" noWrap>
        {data.subtitle}
      </Typography>
      {data.kind !== "provider" && <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />}
    </Box>
  );
});
FlowNode.displayName = "FlowNode";

const NODE_TYPES = { flow: FlowNode };

/** Where the calls in the window went: the gateway, the model names asked for, the providers that answered. */
export default function RoutingMap({ routes: rawRoutes, selected, onFilterModel, now }: Props) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();

  // The gateway counts per instance; a provider can run several (runtimes, cluster replicas): one line each.
  const routes = useMemo(() => {
    const merged = new Map<string, LlmRoute>();
    for (const r of rawRoutes) {
      const key = `${r.model_alias ?? ""}\u0000${providerKey(r)}`;
      const seen = merged.get(key);
      merged.set(
        key,
        seen
          ? {
              ...seen,
              calls: seen.calls + r.calls,
              errors: seen.errors + r.errors,
              tokens: seen.tokens + r.tokens,
              p95_latency_ms: Math.max(seen.p95_latency_ms ?? 0, r.p95_latency_ms ?? 0) || null,
              last_call_at:
                (seen.last_call_at ?? "") > (r.last_call_at ?? "") ? seen.last_call_at : r.last_call_at,
            }
          : { ...r },
      );
    }
    return [...merged.values()].sort((a, b) => b.calls - a.calls);
  }, [rawRoutes]);

  const { nodes, edges } = useMemo(() => {
    const models = new Map<string, { calls: number; errors: number }>();
    const providers = new Map<string, { title: string; calls: number; errors: number; p95: number | null }>();
    for (const r of routes) {
      const m = r.model_alias ?? t("llm_calls.no_model");
      const ms = models.get(m) ?? { calls: 0, errors: 0 };
      models.set(m, { calls: ms.calls + r.calls, errors: ms.errors + r.errors });
      const key = providerKey(r);
      const title =
        r.provider_name ??
        (r.provider_instance_id ? t("llm_calls.instance_short", { id: shortId(r.provider_instance_id) }) : t("llm_calls.not_routed"));
      const ps = providers.get(key) ?? { title, calls: 0, errors: 0, p95: null };
      providers.set(key, { title, calls: ps.calls + r.calls, errors: ps.errors + r.errors, p95: Math.max(ps.p95 ?? 0, r.p95_latency_ms ?? 0) || null });
    }
    const total = routes.reduce((s, r) => s + r.calls, 0);
    const totalErrors = routes.reduce((s, r) => s + r.errors, 0);
    const selModel = selected ? (selected.model_alias ?? t("llm_calls.no_model")) : null;
    const selProvider = selected ? providerKey(selected) : null;
    const rowH = 92;
    const modelList = [...models.entries()].sort((a, b) => b[1].calls - a[1].calls);
    const providerList = [...providers.entries()].sort((a, b) => b[1].calls - a[1].calls);
    const height = Math.max(modelList.length, providerList.length, 1) * rowH;
    const center = (count: number, i: number) => (height - count * rowH) / 2 + i * rowH;

    const nodes: Node<FlowNodeData>[] = [
      {
        id: "gateway",
        type: "flow",
        position: { x: 0, y: height / 2 - rowH / 2 },
        data: {
          kind: "gateway",
          title: t("llm_calls.gateway"),
          subtitle: t("llm_calls.node_stats", { calls: formatCount(total), errors: formatPercent(total ? totalErrors / total : 0) }),
          calls: total,
          errors: totalErrors,
          highlighted: Boolean(selected),
          dimmed: false,
        },
        draggable: false,
      },
      ...modelList.map(([name, s], i) => ({
        id: `model:${name}`,
        type: "flow",
        position: { x: 320, y: center(modelList.length, i) },
        data: {
          kind: "model" as const,
          title: name,
          subtitle: t("llm_calls.node_stats", { calls: formatCount(s.calls), errors: formatPercent(s.calls ? s.errors / s.calls : 0) }),
          calls: s.calls,
          errors: s.errors,
          highlighted: selModel === name,
          dimmed: Boolean(selModel) && selModel !== name,
        },
        draggable: false,
      })),
      ...providerList.map(([key, s], i) => ({
        id: key,
        type: "flow",
        position: { x: 700, y: center(providerList.length, i) },
        data: {
          kind: "provider" as const,
          title: s.title,
          subtitle: t("llm_calls.node_stats_p95", {
            calls: formatCount(s.calls),
            errors: formatPercent(s.calls ? s.errors / s.calls : 0),
            p95: formatDuration(s.p95),
          }),
          calls: s.calls,
          errors: s.errors,
          highlighted: selProvider === key,
          dimmed: Boolean(selProvider) && selProvider !== key,
        },
        draggable: false,
      })),
    ];

    const maxCalls = Math.max(1, ...routes.map((r) => r.calls));
    const edgeColor = (calls: number, errors: number, on: boolean) => {
      if (on) return theme.palette.primary.main;
      const tone = errorTone(calls ? errors / calls : 0);
      return tone === "bad" ? theme.palette.error.main : tone === "warn" ? theme.palette.warning.main : theme.palette.text.disabled;
    };
    const width = (calls: number) => 1 + 4 * Math.sqrt(calls / maxCalls);
    const edges: Edge[] = [
      ...modelList.map(([name, s]) => {
        const on = selModel === name;
        return {
          id: `gw-${name}`,
          source: "gateway",
          target: `model:${name}`,
          animated: on,
          style: { stroke: edgeColor(s.calls, s.errors, on), strokeWidth: width(s.calls), opacity: selModel && !on ? 0.3 : 1 },
        };
      }),
      ...routes.map((r) => {
        const m = r.model_alias ?? t("llm_calls.no_model");
        const key = providerKey(r);
        const on = selModel === m && selProvider === key;
        return {
          id: `route-${m}-${key}`,
          source: `model:${m}`,
          target: key,
          animated: on,
          label: t("llm_calls.edge_label", { calls: formatCount(r.calls), errors: formatPercent(r.calls ? r.errors / r.calls : 0) }),
          labelStyle: { fill: theme.palette.text.secondary, fontSize: 11 },
          labelBgStyle: { fill: theme.palette.background.paper, fillOpacity: 0.9 },
          markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor(r.calls, r.errors, on) },
          style: { stroke: edgeColor(r.calls, r.errors, on), strokeWidth: width(r.calls), opacity: selModel && !on ? 0.3 : 1 },
        };
      }),
    ];
    return { nodes, edges };
  }, [routes, selected, t, theme]);

  if (routes.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 4, textAlign: "center" }}>
        {t("llm_calls.routing_empty")}
      </Typography>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Box sx={{ flex: "1 1 60%", minHeight: 260, borderBottom: 1, borderColor: "divider" }}>
        <ReactFlow
          key={routes.length}
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          nodesDraggable={false}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => {
            if (node.id.startsWith("model:")) onFilterModel(node.id.slice("model:".length));
          }}
          minZoom={0.3}
        >
          <Background color={alpha(theme.palette.text.primary, 0.12)} gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </Box>
      <Box sx={{ flex: "1 1 40%", overflow: "auto", minHeight: 0 }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow sx={{ "& th": { bgcolor: "background.paper", fontWeight: 600, color: "text.secondary" } }}>
              <TableCell>{t("llm_calls.col_model")}</TableCell>
              <TableCell>{t("llm_calls.col_provider")}</TableCell>
              <TableCell align="right">{t("llm_calls.stat_calls")}</TableCell>
              <TableCell align="right">{t("llm_calls.stat_errors")}</TableCell>
              <TableCell align="right">{t("llm_calls.stat_p95")}</TableCell>
              <TableCell align="right">{t("llm_calls.col_tokens")}</TableCell>
              <TableCell>{t("llm_calls.col_last_call")}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {routes.map((r) => {
              const rate = r.calls ? r.errors / r.calls : 0;
              return (
                <TableRow
                  key={`${r.model_alias}-${providerKey(r)}`}
                  hover
                  sx={{ cursor: r.model_alias ? "pointer" : "default" }}
                  onClick={() => r.model_alias && onFilterModel(r.model_alias)}
                >
                  <TableCell>{r.model_alias ?? "—"}</TableCell>
                  <TableCell>
                    {r.provider_name ?? (
                      <Typography variant="body2" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                        {r.provider_instance_id ? shortId(r.provider_instance_id) : t("llm_calls.not_routed")}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell align="right">{formatCount(r.calls)}</TableCell>
                  <TableCell
                    align="right"
                    sx={{ color: errorTone(rate) === "bad" ? "error.main" : errorTone(rate) === "warn" ? "warning.main" : undefined }}
                  >
                    {formatCount(r.errors)} · {formatPercent(rate)}
                  </TableCell>
                  <TableCell align="right">{formatDuration(r.p95_latency_ms)}</TableCell>
                  <TableCell align="right">{formatCount(r.tokens)}</TableCell>
                  <TableCell>{r.last_call_at ? formatAgo(r.last_call_at, now, i18n.language) : "—"}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}
