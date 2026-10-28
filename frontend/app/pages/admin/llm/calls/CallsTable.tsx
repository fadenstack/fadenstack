import { memo, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";
import BuildOutlinedIcon from "@mui/icons-material/BuildOutlined";
import ContentCutIcon from "@mui/icons-material/ContentCut";
import ForumOutlinedIcon from "@mui/icons-material/ForumOutlined";
import HandymanOutlinedIcon from "@mui/icons-material/HandymanOutlined";
import ReplayIcon from "@mui/icons-material/Replay";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";

import type { CallSort, LlmCall } from "~/api/llmCalls";

import { isErrorCall } from "./diagnose";
import {
  callName,
  formatAgo,
  formatCost,
  formatCount,
  formatDuration,
  isAgentCall,
  latencyBand,
  shortId,
} from "./format";

interface Props {
  calls: LlmCall[];
  selectedId: string | null;
  onSelect: (callId: string) => void;
  p95LatencyMs: number | null;
  sort: CallSort;
  onSort: (sort: CallSort) => void;
  /** Request ids that arrived with the last live refresh, briefly marked. */
  fresh: Set<string>;
  now: number;
  /** A call is open beside the list: the least needed columns make room. */
  compact?: boolean;
}

export function StatusDot({ call }: { call: Pick<LlmCall, "status_code" | "error_code"> }) {
  const theme = useTheme();
  const failed = isErrorCall(call);
  const color = failed
    ? call.status_code >= 400 && call.status_code < 500
      ? theme.palette.warning.main
      : theme.palette.error.main
    : theme.palette.success.main;
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.75,
        fontVariantNumeric: "tabular-nums",
        fontWeight: 600,
        color: failed ? color : "text.secondary",
      }}
    >
      <Box component="span" sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: color, flexShrink: 0 }} />
      {call.status_code}
    </Box>
  );
}

export function LatencyBar({ ms, p95, max }: { ms: number; p95: number | null; max: number }) {
  const theme = useTheme();
  const band = latencyBand(ms, p95);
  const color = {
    fast: theme.palette.success.main,
    normal: theme.palette.info.main,
    slow: theme.palette.warning.main,
    outlier: theme.palette.error.main,
  }[band];
  const width = Math.max(2, Math.min(100, (ms / Math.max(1, max)) * 100));
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        variant="body2"
        sx={{
          fontVariantNumeric: "tabular-nums",
          fontWeight: band === "outlier" || band === "slow" ? 600 : 400,
          color: band === "outlier" ? "error.main" : "text.primary",
          lineHeight: 1.3,
        }}
      >
        {formatDuration(ms)}
      </Typography>
      <Box sx={{ mt: 0.25, height: 3, borderRadius: 2, bgcolor: alpha(color, 0.15), overflow: "hidden" }}>
        <Box sx={{ width: `${width}%`, height: "100%", bgcolor: color }} />
      </Box>
    </Box>
  );
}

const Row = memo(function Row({
  call,
  selected,
  fresh,
  onSelect,
  p95,
  max,
  now,
  compact,
}: {
  call: LlmCall;
  selected: boolean;
  fresh: boolean;
  onSelect: (id: string) => void;
  p95: number | null;
  max: number;
  now: number;
  compact: boolean;
}) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const failed = isErrorCall(call);
  const agent = isAgentCall(call.endpoint, call.agent_id);
  return (
    <TableRow
      hover
      selected={selected}
      onClick={() => onSelect(call.id)}
      data-call-id={call.id}
      aria-selected={selected}
      sx={{
        cursor: "pointer",
        "& td": { py: 0.75, borderBottomColor: "divider" },
        ...(failed && !selected ? { bgcolor: alpha(theme.palette.error.main, 0.04) } : {}),
        ...(fresh ? { animation: "callFresh 2.4s ease-out" } : {}),
        "@keyframes callFresh": {
          from: { backgroundColor: alpha(theme.palette.primary.main, 0.22) },
          to: { backgroundColor: "transparent" },
        },
      }}
    >
      <TableCell sx={{ width: 74 }}>
        <StatusDot call={call} />
      </TableCell>
      <TableCell sx={{ width: 104, whiteSpace: "nowrap" }}>
        <Tooltip title={new Date(call.created_at).toLocaleString(i18n.language)} placement="top-start">
          <Typography variant="body2" color="text.secondary" component="span">
            {formatAgo(call.created_at, now, i18n.language)}
          </Typography>
        </Tooltip>
      </TableCell>
      <TableCell sx={{ minWidth: 160, maxWidth: 260 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}>
          {agent && (
            <Tooltip title={t("llm_calls.agent_call")}>
              <SmartToyOutlinedIcon sx={{ fontSize: 16, color: "secondary.main" }} />
            </Tooltip>
          )}
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
            {callName(call.endpoint)}
          </Typography>
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }} noWrap component="div">
          {shortId(call.request_id)}
          {failed && call.error_code ? ` · ${call.error_code}` : ""}
        </Typography>
      </TableCell>
      <TableCell sx={{ minWidth: 140, maxWidth: 240 }}>
        <Typography variant="body2" noWrap>
          {call.model_alias ?? "—"}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap component="div">
          {call.provider_name ?? (call.provider_instance_id ? shortId(call.provider_instance_id) : "—")}
        </Typography>
      </TableCell>
      <TableCell sx={{ width: 130 }}>
        <LatencyBar ms={call.latency_ms} p95={p95} max={max} />
        {call.ttft_ms !== null && (
          <Typography variant="caption" color="text.secondary" component="div" noWrap sx={{ fontVariantNumeric: "tabular-nums" }}>
            {t("llm_calls.ttft_short", { value: formatDuration(call.ttft_ms) })}
          </Typography>
        )}
      </TableCell>
      <TableCell sx={{ width: 120, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
        {call.total_tokens === null && call.prompt_tokens === null ? (
          <Typography variant="body2" color="text.disabled">
            —
          </Typography>
        ) : (
          <Tooltip
            title={t("llm_calls.tokens_tooltip", {
              prompt: call.prompt_tokens ?? 0,
              completion: call.completion_tokens ?? 0,
              total: call.total_tokens ?? 0,
            })}
          >
            <Typography variant="body2" component="span">
              {formatCount(call.prompt_tokens)}
              <Box component="span" sx={{ color: "text.secondary", mx: 0.5 }}>
                →
              </Box>
              {formatCount(call.completion_tokens)}
            </Typography>
          </Tooltip>
        )}
      </TableCell>
      {!compact && (
        <TableCell sx={{ width: 90, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
          <Typography variant="body2" color={call.estimated_total_cost == null ? "text.disabled" : "text.primary"}>
            {formatCost(call.estimated_total_cost, call.currency)}
          </Typography>
        </TableCell>
      )}
      <TableCell sx={{ width: 120 }}>
        <Box sx={{ display: "flex", gap: 0.5, alignItems: "center", color: "text.secondary" }}>
          {(call.mcp_tool_call_count ?? 0) > 0 && (
            <Tooltip title={t("llm_calls.flag_tools", { count: call.mcp_tool_call_count ?? 0 })}>
              <Chip
                size="small"
                variant="outlined"
                icon={<BuildOutlinedIcon sx={{ fontSize: "14px !important" }} />}
                label={call.mcp_tool_call_count}
                sx={{ height: 20, "& .MuiChip-label": { px: 0.5 } }}
              />
            </Tooltip>
          )}
          {call.finish_reason === "tool_calls" && (
            <Tooltip title={t("llm_calls.flag_requested_tools")}>
              <HandymanOutlinedIcon sx={{ fontSize: 16, color: "info.main" }} />
            </Tooltip>
          )}
          {call.finish_reason === "length" && (
            <Tooltip title={t("llm_calls.flag_truncated")}>
              <ContentCutIcon sx={{ fontSize: 16, color: "warning.main" }} />
            </Tooltip>
          )}
          {(call.retry_count ?? 0) > 0 && (
            <Tooltip title={t("llm_calls.flag_retried", { count: call.retry_count ?? 0 })}>
              <ReplayIcon sx={{ fontSize: 16 }} />
            </Tooltip>
          )}
          {(call.conversation_id || call.session_id) && (
            <Tooltip title={t("llm_calls.flag_conversation")}>
              <ForumOutlinedIcon sx={{ fontSize: 16 }} />
            </Tooltip>
          )}
        </Box>
      </TableCell>
    </TableRow>
  );
});

/** Calls, newest first by default; a click opens one beside the list. */
export default function CallsTable({ calls, selectedId, onSelect, p95LatencyMs, sort, onSort, fresh, now, compact = false }: Props) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLTableSectionElement | null>(null);
  // Bars scale to a little past p95, so one 15-minute timeout does not flatten every other bar.
  const max = p95LatencyMs ? p95LatencyMs * 1.5 : Math.max(1, ...calls.map((c) => c.latency_ms));

  useEffect(() => {
    if (!selectedId) return;
    const row = containerRef.current?.querySelector<HTMLElement>(`[data-call-id="${CSS.escape(selectedId)}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const sortable = (label: string, key: CallSort, alt?: CallSort) => (
    <TableSortLabel
      active={sort === key || sort === alt}
      direction={sort === "oldest" ? "asc" : "desc"}
      onClick={() => onSort(sort === key && alt ? alt : key)}
    >
      {label}
    </TableSortLabel>
  );

  return (
    <Table size="small" stickyHeader aria-label={t("llm_calls.title")} sx={{ tableLayout: "auto" }}>
      <TableHead>
        <TableRow
          sx={{
            "& th": {
              bgcolor: "background.paper",
              fontWeight: 600,
              fontSize: "0.75rem",
              color: "text.secondary",
              textTransform: "uppercase",
              letterSpacing: 0.4,
              whiteSpace: "nowrap",
            },
          }}
        >
          <TableCell>{t("llm_calls.col_status")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_time"), "newest", "oldest")}</TableCell>
          <TableCell>{t("llm_calls.col_name")}</TableCell>
          <TableCell>{t("llm_calls.col_model")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_latency"), "slowest")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_tokens"), "tokens")}</TableCell>
          {!compact && <TableCell>{t("llm_calls.col_cost")}</TableCell>}
          <TableCell>{t("llm_calls.col_flags")}</TableCell>
          {/* No person column: calls name nobody (usage of single people is not shown here). */}
        </TableRow>
      </TableHead>
      <TableBody ref={containerRef}>
        {calls.map((call) => (
          <Row
            key={call.id}
            call={call}
            selected={call.id === selectedId}
            fresh={fresh.has(call.id)}
            onSelect={onSelect}
            p95={p95LatencyMs}
            max={max}
            now={now}
            compact={compact}
          />
        ))}
      </TableBody>
    </Table>
  );
}
