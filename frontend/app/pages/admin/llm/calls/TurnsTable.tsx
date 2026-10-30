import { Fragment, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
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
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import GppMaybeOutlinedIcon from "@mui/icons-material/GppMaybeOutlined";
import HandymanOutlinedIcon from "@mui/icons-material/HandymanOutlined";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";

import { llmCalls, type CallSort, type LlmCall, type LlmTurn } from "~/api/llmCalls";

import { LatencyBar, StatusDot } from "./CallsTable";
import { callName, formatAgo, formatCount, formatDuration, isAgentCall, shortId } from "./format";

interface Props {
  turns: LlmTurn[];
  selectedId: string | null;
  onSelect: (callId: string) => void;
  p95LatencyMs: number | null;
  sort: CallSort;
  onSort: (sort: CallSort) => void;
  fresh: Set<string>;
  now: number;
  compact?: boolean;
}

/** The turn's outcome: its worst call when one failed, else its last. */
function turnStatus(turn: LlmTurn) {
  return { status_code: turn.errors > 0 ? turn.worst_status : turn.status_code, error_code: turn.error_code };
}

function ToolSignals({ turn }: { turn: LlmTurn }) {
  const { t } = useTranslation();
  const tools = turn.client_tools + turn.server_tools;
  const failed = turn.client_tool_errors;
  return (
    <Box sx={{ display: "flex", gap: 0.5, alignItems: "center", color: "text.secondary", flexWrap: "nowrap" }}>
      {tools > 0 ? (
        <Tooltip
          title={t("llm_calls.turn_tools_tooltip", {
            app: turn.client_tools,
            gateway: turn.server_tools,
            failed,
          })}
        >
          <Chip
            size="small"
            variant="outlined"
            color={failed > 0 ? "error" : "default"}
            icon={<BuildOutlinedIcon sx={{ fontSize: "14px !important" }} />}
            label={failed > 0 ? `${tools} · ${failed}✕` : tools}
            sx={{ height: 20, "& .MuiChip-label": { px: 0.5 } }}
          />
        </Tooltip>
      ) : turn.tool_rounds > 0 ? (
        // The model asked for tools, but the app reported none (an older SDK, or events not sent yet).
        <Tooltip title={t("llm_calls.turn_tool_rounds_tooltip", { count: turn.tool_rounds })}>
          <HandymanOutlinedIcon sx={{ fontSize: 16, color: "info.main" }} />
        </Tooltip>
      ) : null}
      {turn.approvals > 0 && (
        <Tooltip
          title={t("llm_calls.turn_approvals_tooltip", {
            count: turn.approvals,
            denied: turn.approvals_denied,
            wait: formatDuration(turn.approval_wait_ms),
          })}
        >
          <Chip
            size="small"
            variant="outlined"
            color={turn.approvals_denied > 0 ? "warning" : "default"}
            icon={<GppMaybeOutlinedIcon sx={{ fontSize: "14px !important" }} />}
            label={turn.approvals}
            sx={{ height: 20, "& .MuiChip-label": { px: 0.5 } }}
          />
        </Tooltip>
      )}
    </Box>
  );
}

/**
 * Requests: the calls an agent's app made for one user request, one row each, opened to show its rounds.
 * A call that belongs to no turn is a request of its own.
 */
export default function TurnsTable({ turns, selectedId, onSelect, p95LatencyMs, sort, onSort, fresh, now, compact = false }: Props) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [children, setChildren] = useState<Record<string, LlmCall[] | "loading">>({});
  const bodyRef = useRef<HTMLTableSectionElement | null>(null);
  const max = p95LatencyMs ? p95LatencyMs * 1.5 : Math.max(1, ...turns.map((x) => x.duration_ms));

  const load = (turn: LlmTurn) => {
    const cached = children[turn.key];
    // A finished turn's calls do not change; a turn still running gets more on the next open.
    if (cached === "loading" || (cached && cached.length === turn.calls)) return;
    setChildren((c) => ({ ...c, [turn.key]: "loading" }));
    llmCalls
      .detail(turn.last_call_id)
      .then((d) => setChildren((c) => ({ ...c, [turn.key]: d.turn_calls })))
      .catch(() => setChildren((c) => ({ ...c, [turn.key]: [] })));
  };

  const toggle = (turn: LlmTurn, value?: boolean) => {
    const next = value ?? !open[turn.key];
    setOpen((o) => ({ ...o, [turn.key]: next }));
    if (next) load(turn);
  };

  useEffect(() => {
    if (!selectedId) return;
    const row = bodyRef.current?.querySelector<HTMLElement>(`[data-call-id="${CSS.escape(selectedId)}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  // A turn still running gets more rounds while it is open: they are fetched as the list refreshes.
  useEffect(() => {
    for (const turn of turns) {
      const cached = children[turn.key];
      if (open[turn.key] && Array.isArray(cached) && cached.length !== turn.calls) load(turn);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turns]);

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
    <Table size="small" stickyHeader aria-label={t("llm_calls.group_turns")}>
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
          <TableCell sx={{ width: 28, px: 0.5 }} />
          <TableCell>{t("llm_calls.col_status")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_time"), "newest", "oldest")}</TableCell>
          <TableCell>{t("llm_calls.col_request")}</TableCell>
          <TableCell>{t("llm_calls.col_model")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_duration"), "slowest")}</TableCell>
          <TableCell>{sortable(t("llm_calls.col_tokens"), "tokens")}</TableCell>
          <TableCell>{t("llm_calls.col_tools")}</TableCell>
          {/* No person column: turns name nobody (usage of single people is not shown here). */}
        </TableRow>
      </TableHead>
      <TableBody ref={bodyRef}>
        {turns.map((turn) => {
          const kids = children[turn.key];
          const isOpen = Boolean(open[turn.key]);
          const kidList = Array.isArray(kids) ? kids : [];
          const selected = turn.last_call_id === selectedId || kidList.some((c) => c.id === selectedId);
          const multi = turn.calls > 1;
          const agent = isAgentCall(turn.endpoint, turn.agent_id);
          const failed = turn.errors > 0;
          return (
            <Fragment key={turn.key}>
              <TableRow
                hover
                selected={selected && !isOpen}
                data-call-id={turn.last_call_id}
                onClick={() => {
                  onSelect(turn.last_call_id);
                  if (multi && !isOpen) toggle(turn, true);
                }}
                sx={{
                  cursor: "pointer",
                  "& td": { py: 0.75, borderBottomColor: "divider" },
                  ...(failed && !selected ? { bgcolor: alpha(theme.palette.error.main, 0.04) } : {}),
                  ...(fresh.has(turn.key) ? { animation: "turnFresh 2.4s ease-out" } : {}),
                  "@keyframes turnFresh": {
                    from: { backgroundColor: alpha(theme.palette.primary.main, 0.22) },
                    to: { backgroundColor: "transparent" },
                  },
                }}
              >
                <TableCell sx={{ width: 28, px: 0.5 }}>
                  {multi && (
                    <IconButton
                      size="small"
                      aria-label={isOpen ? t("llm_calls.collapse") : t("llm_calls.expand")}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(turn);
                      }}
                      sx={{ p: 0.25 }}
                    >
                      {isOpen ? <ExpandMoreIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />}
                    </IconButton>
                  )}
                </TableCell>
                <TableCell sx={{ width: 74 }}>
                  <StatusDot call={turnStatus(turn)} />
                </TableCell>
                <TableCell sx={{ width: 104, whiteSpace: "nowrap" }}>
                  <Tooltip title={new Date(turn.ended_at).toLocaleString(i18n.language)} placement="top-start">
                    <Typography variant="body2" color="text.secondary" component="span">
                      {formatAgo(turn.ended_at, now, i18n.language)}
                    </Typography>
                  </Tooltip>
                </TableCell>
                <TableCell sx={{ minWidth: 170, maxWidth: 280 }}>
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}>
                    {agent && (
                      <Tooltip title={t("llm_calls.agent_call")}>
                        <SmartToyOutlinedIcon sx={{ fontSize: 16, color: "secondary.main" }} />
                      </Tooltip>
                    )}
                    <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
                      {multi ? t("llm_calls.turn_rounds", { count: turn.calls }) : callName(turn.endpoint)}
                    </Typography>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }} noWrap component="div">
                    {shortId(turn.turn_id ?? turn.last_call_id)}
                    {failed && turn.error_code ? ` · ${turn.error_code}` : ""}
                  </Typography>
                </TableCell>
                <TableCell sx={{ minWidth: 140, maxWidth: 240 }}>
                  <Typography variant="body2" noWrap>
                    {turn.model_alias ?? "—"}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap component="div">
                    {turn.provider_name ?? (turn.provider_instance_id ? shortId(turn.provider_instance_id) : "—")}
                  </Typography>
                </TableCell>
                <TableCell sx={{ width: 140 }}>
                  <LatencyBar ms={turn.duration_ms} p95={p95LatencyMs} max={max} />
                  {multi && (
                    <Typography variant="caption" color="text.secondary" component="div" noWrap sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {t("llm_calls.turn_llm_time", { value: formatDuration(turn.llm_ms) })}
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={{ width: 120, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                  {turn.total_tokens === null && turn.prompt_tokens === null ? (
                    <Typography variant="body2" color="text.disabled">
                      —
                    </Typography>
                  ) : (
                    <Typography variant="body2" component="span">
                      {formatCount(turn.prompt_tokens)}
                      <Box component="span" sx={{ color: "text.secondary", mx: 0.5 }}>
                        →
                      </Box>
                      {formatCount(turn.completion_tokens)}
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={{ width: 130 }}>
                  <ToolSignals turn={turn} />
                </TableCell>
              </TableRow>
              {isOpen && kids === "loading" && (
                <TableRow>
                  <TableCell colSpan={8} sx={{ py: 1, pl: 6 }}>
                    <CircularProgress size={16} />
                  </TableCell>
                </TableRow>
              )}
              {isOpen &&
                kidList.map((c, i) => (
                  <TableRow
                    key={c.id}
                    hover
                    selected={c.id === selectedId}
                    data-call-id={c.id}
                    onClick={() => onSelect(c.id)}
                    sx={{ cursor: "pointer", "& td": { py: 0.5, borderBottomColor: "divider" }, bgcolor: "action.hover" }}
                  >
                    <TableCell sx={{ px: 0.5 }} />
                    <TableCell sx={{ pl: 3 }}>
                      <StatusDot call={c} />
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography variant="caption" color="text.secondary">
                        {t("llm_calls.round_n", { n: i + 1 })}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0 }}>
                        <Typography variant="body2" noWrap>
                          {callName(c.endpoint)}
                        </Typography>
                        {c.finish_reason === "tool_calls" && (
                          <Tooltip title={t("llm_calls.flag_requested_tools")}>
                            <HandymanOutlinedIcon sx={{ fontSize: 15, color: "info.main" }} />
                          </Tooltip>
                        )}
                        {c.finish_reason && (
                          <Typography variant="caption" color="text.secondary" noWrap>
                            {c.finish_reason}
                          </Typography>
                        )}
                      </Box>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary" noWrap>
                        {c.model_alias ?? "—"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <LatencyBar ms={c.latency_ms} p95={p95LatencyMs} max={max} />
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                      <Typography variant="caption">
                        {formatCount(c.prompt_tokens)} → {formatCount(c.completion_tokens)}
                      </Typography>
                    </TableCell>
                    <TableCell />
                    {!compact && <TableCell />}
                  </TableRow>
                ))}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}
