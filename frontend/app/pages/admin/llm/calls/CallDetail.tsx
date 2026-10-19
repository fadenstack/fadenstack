import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import FilterAltOutlinedIcon from "@mui/icons-material/FilterAltOutlined";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import KeyboardArrowUpIcon from "@mui/icons-material/KeyboardArrowUp";
import LinkIcon from "@mui/icons-material/Link";

import { llmCalls, type LlmCall, type LlmCallDetail, type LlmCallTool } from "~/api/llmCalls";

import { StatusDot } from "./CallsTable";
import { diagnose, isErrorCall, type Finding } from "./diagnose";
import {
  callName,
  formatCost,
  formatCount,
  formatDuration,
  generationSpeed,
  isAgentCall,
} from "./format";

interface Props {
  /** The call's log row id: a request id is the caller's to choose, and not unique. */
  callId: string;
  /** The row from the list, shown while the full record loads. */
  preview: LlmCall | null;
  p95LatencyMs: number | null;
  onClose: () => void;
  onSelect: (callId: string) => void;
  onPrev?: () => void;
  onNext?: () => void;
  onFilter: (key: "session" | "user_id" | "model_alias", value: string) => void;
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text);
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Tooltip title={done ? "✓" : label}>
      <IconButton
        size="small"
        aria-label={label}
        onClick={(e) => {
          e.stopPropagation();
          copy(value);
          setDone(true);
          window.setTimeout(() => setDone(false), 1200);
        }}
        sx={{ p: 0.25 }}
      >
        <ContentCopyIcon sx={{ fontSize: 14 }} />
      </IconButton>
    </Tooltip>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "error" | "warning" }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" component="div" noWrap>
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{ fontWeight: 600, fontVariantNumeric: "tabular-nums", color: tone ? `${tone}.main` : "text.primary" }}
        noWrap
      >
        {value}
      </Typography>
    </Box>
  );
}

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <Box sx={{ mt: 2 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 0.75 }}>
        <Typography variant="overline" color="text.secondary" sx={{ lineHeight: 1.5, letterSpacing: 0.8 }}>
          {title}
        </Typography>
        {action}
      </Stack>
      {children}
    </Box>
  );
}

function Findings({ findings }: { findings: Finding[] }) {
  const { t } = useTranslation();
  if (findings.length === 0) return null;
  return (
    <Stack spacing={1} sx={{ mt: 2 }}>
      {findings.map((f) => (
        <Alert key={f.id} severity={f.severity} variant="outlined" sx={{ py: 0.25, "& .MuiAlert-message": { py: 0.75 } }}>
          <AlertTitle sx={{ mb: 0.25, fontSize: "0.875rem" }}>{t(`llm_calls.diag.${f.id}`, f.params)}</AlertTitle>
          <Typography variant="body2" color="text.secondary">
            {t(`llm_calls.diag.${f.id}_hint`, f.params)}
          </Typography>
        </Alert>
      ))}
    </Stack>
  );
}

interface Span {
  key: string;
  label: string;
  sub?: string;
  start: number;
  duration: number;
  color: string;
  error?: boolean;
  depth: number;
}

/** A waterfall of the call: the whole, its wait for the first token, generation, and tool calls. */
function Timeline({ call, tools }: { call: LlmCall; tools: LlmCallTool[] }) {
  const { t } = useTranslation();
  const theme = useTheme();
  const total = Math.max(1, call.latency_ms);
  const failed = isErrorCall(call);
  const spans: Span[] = [
    {
      key: "call",
      label: callName(call.endpoint),
      sub: call.model_alias ?? undefined,
      start: 0,
      duration: call.latency_ms,
      color: failed ? theme.palette.error.main : theme.palette.primary.main,
      error: failed,
      depth: 0,
    },
  ];
  if (call.ttft_ms !== null && call.ttft_ms > 0) {
    spans.push({
      key: "ttft",
      label: t("llm_calls.span_first_token"),
      start: 0,
      duration: call.ttft_ms,
      color: theme.palette.warning.main,
      depth: 1,
    });
    spans.push({
      key: "gen",
      label: t("llm_calls.span_generation"),
      sub: (() => {
        const speed = generationSpeed(call.completion_tokens, call.latency_ms, call.ttft_ms);
        return speed ? t("llm_calls.tokens_per_sec", { value: speed.toFixed(1) }) : undefined;
      })(),
      start: call.ttft_ms,
      duration: Math.max(0, call.latency_ms - call.ttft_ms),
      color: theme.palette.success.main,
      depth: 1,
    });
  }
  // The gateway records each tool's duration and round, not when it started:
  // rounds are laid end to end, the tools of a round side by side.
  let offset = 0;
  const rounds = new Map<number, LlmCallTool[]>();
  for (const tool of tools) rounds.set(tool.iteration, [...(rounds.get(tool.iteration) ?? []), tool]);
  for (const [round, list] of [...rounds.entries()].sort((a, b) => a[0] - b[0])) {
    let longest = 0;
    for (const tool of list) {
      const duration = tool.latency_ms ?? 0;
      longest = Math.max(longest, duration);
      spans.push({
        key: tool.id,
        label: tool.tool_name,
        sub: [t("llm_calls.span_round", { round: round + 1 }), tool.mcp_server, tool.error_kind]
          .filter(Boolean)
          .join(" · "),
        start: Math.min(offset, total),
        duration,
        color: tool.is_error ? theme.palette.error.main : theme.palette.secondary.main,
        error: tool.is_error,
        depth: 1,
      });
    }
    offset += longest;
  }
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <Box>
      <Box sx={{ display: "grid", gridTemplateColumns: "minmax(120px, 38%) 1fr", columnGap: 1.5, rowGap: 0.5 }}>
        <Box />
        <Box sx={{ position: "relative", height: 16 }}>
          {ticks.map((tick) => (
            <Typography
              key={tick}
              variant="caption"
              color="text.secondary"
              sx={{
                position: "absolute",
                left: `${tick * 100}%`,
                transform: tick === 0 ? "none" : tick === 1 ? "translateX(-100%)" : "translateX(-50%)",
                fontSize: "0.68rem",
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
              }}
            >
              {formatDuration(total * tick)}
            </Typography>
          ))}
        </Box>
        {spans.map((span) => (
          <TimelineRow key={span.key} span={span} total={total} />
        ))}
      </Box>
      {tools.length > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          {t("llm_calls.timeline_tools_note")}
        </Typography>
      )}
    </Box>
  );
}

function TimelineRow({ span, total }: { span: Span; total: number }) {
  const left = Math.min(100, (span.start / total) * 100);
  const width = Math.max(0.6, Math.min(100 - left, (span.duration / total) * 100));
  return (
    <>
      <Box sx={{ pl: span.depth * 1.5, minWidth: 0, display: "flex", alignItems: "baseline", gap: 0.75 }}>
        <Typography variant="body2" noWrap sx={{ fontWeight: span.depth === 0 ? 600 : 400, color: span.error ? "error.main" : undefined }}>
          {span.label}
        </Typography>
        {span.sub && (
          <Typography variant="caption" color="text.secondary" noWrap>
            {span.sub}
          </Typography>
        )}
      </Box>
      <Box sx={{ position: "relative", height: 20, borderRadius: 0.5, bgcolor: "action.hover" }}>
        <Tooltip title={formatDuration(span.duration)}>
          <Box
            sx={{
              position: "absolute",
              left: `${left}%`,
              width: `${width}%`,
              top: 3,
              bottom: 3,
              borderRadius: 0.5,
              bgcolor: alpha(span.color, 0.85),
            }}
          />
        </Tooltip>
        <Typography
          variant="caption"
          sx={{
            position: "absolute",
            top: 1,
            fontSize: "0.68rem",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
            ...(width >= 15
              ? { right: `${Math.max(0, 100 - left - width)}%`, pr: 0.75, color: "common.white", fontWeight: 600 }
              : left + width > 70
                ? { right: `${100 - left}%`, pr: 0.5, color: "text.primary" }
                : { left: `${left + width}%`, pl: 0.5, color: "text.primary" }),
          }}
        >
          {formatDuration(span.duration)}
        </Typography>
      </Box>
    </>
  );
}

function Field({ label, value, mono, onFilter }: { label: string; value: ReactNode; mono?: boolean; onFilter?: () => void }) {
  const { t } = useTranslation();
  const empty = value === null || value === undefined || value === "";
  return (
    <>
      <Typography variant="body2" color="text.secondary" sx={{ py: 0.25 }}>
        {label}
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0, py: 0.25 }}>
        <Typography
          variant="body2"
          sx={{ fontFamily: mono ? "monospace" : undefined, wordBreak: "break-all", color: empty ? "text.disabled" : undefined }}
        >
          {empty ? "—" : value}
        </Typography>
        {!empty && typeof value === "string" && mono && <CopyButton value={value} label={t("llm_calls.copy")} />}
        {!empty && onFilter && (
          <Tooltip title={t("llm_calls.filter_by")}>
            <IconButton size="small" onClick={onFilter} sx={{ p: 0.25 }} aria-label={t("llm_calls.filter_by")}>
              <FilterAltOutlinedIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        )}
      </Box>
    </>
  );
}

function FieldGrid({ children }: { children: ReactNode }) {
  return <Box sx={{ display: "grid", gridTemplateColumns: "minmax(110px, 34%) 1fr", columnGap: 1.5 }}>{children}</Box>;
}

function Details({ call, onFilter }: { call: LlmCall; onFilter: Props["onFilter"] }) {
  const { t } = useTranslation();
  const skills = (call.skills_used ?? []).map((s) => s.name ?? s.slug).filter(Boolean).join(", ");
  const rag = call.rag_context
    ? t("llm_calls.rag_value", { chunks: call.rag_context.chunk_count ?? 0, top_k: call.rag_context.top_k ?? 0 })
    : null;
  return (
    <>
      <Section title={t("llm_calls.section_request")}>
        <FieldGrid>
          <Field label={t("llm_calls.f_request_id")} value={call.request_id} mono />
          <Field label={t("llm_calls.f_trace_id")} value={call.trace_id} mono />
          <Field label={t("llm_calls.f_endpoint")} value={call.endpoint} mono />
          <Field label={t("llm_calls.f_time")} value={new Date(call.created_at).toLocaleString()} />
          <Field label={t("llm_calls.f_stream")} value={call.stream == null ? null : call.stream ? t("common.yes") : t("common.no")} />
          <Field label={t("llm_calls.f_finish")} value={call.finish_reason} />
          <Field label={t("llm_calls.f_error")} value={call.error_code} mono />
          <Field label={t("llm_calls.f_retries")} value={String(call.retry_count ?? 0)} />
        </FieldGrid>
      </Section>
      <Section title={t("llm_calls.section_routing")}>
        <FieldGrid>
          <Field label={t("llm_calls.f_model")} value={call.model_alias} onFilter={call.model_alias ? () => onFilter("model_alias", call.model_alias!) : undefined} />
          <Field label={t("llm_calls.f_provider")} value={call.provider_name} />
          <Field label={t("llm_calls.f_instance")} value={call.provider_instance_id} mono />
        </FieldGrid>
      </Section>
      <Section title={t("llm_calls.section_who")}>
        <FieldGrid>
          <Field label={t("llm_calls.f_user")} value={call.user_id} mono onFilter={() => onFilter("user_id", call.user_id)} />
          <Field label={t("llm_calls.f_tenant")} value={call.tenant_id} />
          <Field label={t("llm_calls.f_group")} value={call.group_id} mono />
          <Field label={t("llm_calls.f_project")} value={call.project_id} mono />
          <Field label={t("llm_calls.f_agent")} value={call.agent_id} mono />
          <Field label={t("llm_calls.f_device")} value={call.device_id} mono />
        </FieldGrid>
      </Section>
      <Section title={t("llm_calls.section_conversation")}>
        <FieldGrid>
          <Field label={t("llm_calls.f_session")} value={call.session_id} mono onFilter={call.session_id ? () => onFilter("session", call.session_id!) : undefined} />
          <Field
            label={t("llm_calls.f_conversation")}
            value={call.conversation_id}
            mono
            onFilter={call.conversation_id ? () => onFilter("session", call.conversation_id!) : undefined}
          />
          <Field label={t("llm_calls.f_turn")} value={call.turn_id} mono />
        </FieldGrid>
      </Section>
      <Section title={t("llm_calls.section_context")}>
        <FieldGrid>
          <Field label={t("llm_calls.f_skills")} value={skills || null} />
          <Field label={t("llm_calls.f_rag")} value={rag} />
          <Field
            label={t("llm_calls.f_tools")}
            value={
              (call.mcp_tool_call_count ?? 0) > 0
                ? t("llm_calls.tools_value", { count: call.mcp_tool_call_count ?? 0, rounds: call.mcp_tool_loop_iterations ?? 0 })
                : null
            }
          />
          <Field label={t("llm_calls.f_cached")} value={call.cached_tokens != null ? formatCount(call.cached_tokens) : null} />
        </FieldGrid>
      </Section>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 2 }}>
        {t("llm_calls.privacy_note")}
      </Typography>
    </>
  );
}

function Related({ detail, current, onSelect }: { detail: LlmCallDetail; current: string; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const theme = useTheme();
  if (detail.related.length < 2) return null;
  const first = new Date(detail.related[0].created_at).getTime() - detail.related[0].latency_ms;
  const totalTokens = detail.related.reduce((sum, c) => sum + (c.total_tokens ?? 0), 0);
  const errors = detail.related.filter(isErrorCall).length;
  return (
    <Section
      title={t(`llm_calls.related_${detail.related_by ?? "trace_id"}`, { count: detail.related.length })}
    >
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 0.75 }}>
        {t("llm_calls.related_summary", {
          tokens: formatCount(totalTokens),
          errors,
        })}
      </Typography>
      <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
        {detail.related.map((c, i) => {
          const active = c.id === current;
          const startOffset = new Date(c.created_at).getTime() - c.latency_ms - first;
          return (
            <Box
              key={c.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(c.id)}
              onKeyDown={(e) => e.key === "Enter" && onSelect(c.id)}
              sx={{
                display: "grid",
                gridTemplateColumns: "28px 64px 1fr auto auto",
                alignItems: "center",
                gap: 1,
                px: 1,
                py: 0.5,
                cursor: "pointer",
                borderTop: i === 0 ? 0 : 1,
                borderColor: "divider",
                bgcolor: active ? alpha(theme.palette.primary.main, 0.12) : undefined,
                "&:hover": { bgcolor: active ? undefined : "action.hover" },
              }}
            >
              <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {i + 1}
              </Typography>
              <StatusDot call={c} />
              <Typography variant="body2" noWrap sx={{ fontWeight: active ? 600 : 400 }}>
                {callName(c.endpoint)}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                  +{formatDuration(Math.max(0, startOffset))}
                </Typography>
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {formatCount(c.total_tokens)} tok
              </Typography>
              <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums", minWidth: 64, textAlign: "right" }}>
                {formatDuration(c.latency_ms)}
              </Typography>
            </Box>
          );
        })}
      </Box>
    </Section>
  );
}

/** One call, opened beside the list: what went wrong first, then where the time went, then every field. */
export default function CallDetail({ callId, preview, p95LatencyMs, onClose, onSelect, onPrev, onNext, onFilter }: Props) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<LlmCallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"overview" | "details" | "json">("overview");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    llmCalls
      .detail(callId, controller.signal)
      .then((d) => setDetail(d))
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setDetail(null);
        setError(e instanceof Error ? e.message : t("llm_calls.failed_detail"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [callId, t]);

  const call = detail?.call.id === callId ? detail.call : preview;
  const current = detail?.call.id === callId ? detail : null;
  const findings = useMemo(
    () =>
      call
        ? diagnose(call, { tools: current?.tool_calls, policies: current?.policy_decisions, p95LatencyMs })
        : [],
    [call, current, p95LatencyMs],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (e.key === "Escape") onClose();
      else if ((e.key === "j" || e.key === "ArrowDown") && onNext) {
        e.preventDefault();
        onNext();
      } else if ((e.key === "k" || e.key === "ArrowUp") && onPrev) {
        e.preventDefault();
        onPrev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onNext, onPrev]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Header */}
      <Box sx={{ px: 2, pt: 1.5, pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          {call && <StatusDot call={call} />}
          <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1, minWidth: 0 }} noWrap>
            {call ? callName(call.endpoint) : t("common.loading")}
            {call && isAgentCall(call.endpoint, call.agent_id) && (
              <Chip size="small" label={t("llm_calls.agent")} color="secondary" variant="outlined" sx={{ ml: 1, height: 20 }} />
            )}
          </Typography>
          <Tooltip title={t("llm_calls.prev")}>
            <span>
              <IconButton size="small" onClick={onPrev} disabled={!onPrev} aria-label={t("llm_calls.prev")}>
                <KeyboardArrowUpIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={t("llm_calls.next")}>
            <span>
              <IconButton size="small" onClick={onNext} disabled={!onNext} aria-label={t("llm_calls.next")}>
                <KeyboardArrowDownIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title={t("llm_calls.copy_link")}>
            <IconButton size="small" onClick={() => copy(window.location.href)} aria-label={t("llm_calls.copy_link")}>
              <LinkIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.close")}>
            <IconButton size="small" onClick={onClose} aria-label={t("common.close")}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
        {call && (
          <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mt: 0.25, minWidth: 0 }}>
            <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
              {call.model_alias ?? "—"}
            </Typography>
            <Typography variant="body2" color="text.secondary" noWrap>
              {call.provider_name
                ? `→ ${call.provider_name}`
                : call.provider_instance_id
                  ? `→ ${t("llm_calls.instance_short", { id: call.provider_instance_id.slice(0, 8) })}`
                  : ""}
            </Typography>
          </Stack>
        )}
        {call && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.25, color: "text.secondary", minWidth: 0 }}>
            <Typography variant="caption" sx={{ fontFamily: "monospace" }} noWrap>
              {call.request_id}
            </Typography>
            <CopyButton value={call.request_id} label={t("llm_calls.copy")} />
            <Typography variant="caption" noWrap>
              · {new Date(call.created_at).toLocaleString()}
            </Typography>
          </Stack>
        )}
      </Box>
      {loading && <LinearProgress sx={{ height: 2 }} />}
      <Divider />
      {call && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(92px, 1fr))",
            gap: 1.25,
            bgcolor: "action.hover",
          }}
        >
          <Metric
            label={t("llm_calls.m_latency")}
            value={formatDuration(call.latency_ms)}
            tone={p95LatencyMs && call.latency_ms > p95LatencyMs * 1.5 ? "error" : undefined}
          />
          <Metric label={t("llm_calls.m_ttft")} value={formatDuration(call.ttft_ms)} />
          <Metric label={t("llm_calls.m_in")} value={formatCount(call.prompt_tokens)} />
          <Metric label={t("llm_calls.m_out")} value={formatCount(call.completion_tokens)} />
          <Metric
            label={t("llm_calls.m_speed")}
            value={(() => {
              const s = generationSpeed(call.completion_tokens, call.latency_ms, call.ttft_ms);
              return s ? t("llm_calls.tokens_per_sec", { value: s.toFixed(1) }) : "—";
            })()}
          />
          <Metric label={t("llm_calls.m_cost")} value={formatCost(call.estimated_total_cost, call.currency)} />
          <Metric
            label={t("llm_calls.m_finish")}
            value={call.finish_reason ?? "—"}
            tone={call.finish_reason === "length" ? "warning" : undefined}
          />
        </Box>
      )}
      <Tabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        sx={{ px: 1, minHeight: 38, borderBottom: 1, borderColor: "divider", "& .MuiTab-root": { minHeight: 38, textTransform: "none" } }}
      >
        <Tab value="overview" label={t("llm_calls.tab_overview")} />
        <Tab value="details" label={t("llm_calls.tab_details")} />
        <Tab value="json" label={t("llm_calls.tab_json")} />
      </Tabs>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, pb: 3 }}>
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        {call && tab === "overview" && (
          <>
            <Findings findings={findings} />
            {findings.length === 0 && current && (
              <Alert severity="success" variant="outlined" sx={{ mt: 2, py: 0 }}>
                {t("llm_calls.diag_none")}
              </Alert>
            )}
            <Section title={t("llm_calls.section_timeline")}>
              <Timeline call={call} tools={current?.tool_calls ?? []} />
            </Section>
            {current && current.policy_decisions.length > 0 && (
              <Section title={t("llm_calls.section_policies")}>
                <Stack spacing={0.5}>
                  {current.policy_decisions.map((p, i) => (
                    <Stack key={i} direction="row" spacing={1} alignItems="center">
                      <Chip
                        size="small"
                        label={p.outcome}
                        color={p.outcome === "block" || p.outcome === "rejected" ? "error" : p.outcome === "approved" ? "success" : "warning"}
                        variant="outlined"
                        sx={{ height: 20 }}
                      />
                      <Typography variant="body2">
                        {t("llm_calls.policy_line", { stage: p.stage, category: p.category, check: p.check_name })}
                      </Typography>
                      {p.tool_id && (
                        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                          {p.tool_id}
                        </Typography>
                      )}
                    </Stack>
                  ))}
                </Stack>
              </Section>
            )}
            {current && (
              <Related detail={current} current={callId} onSelect={onSelect} />
            )}
            {current && current.related.length < 2 && (call.session_id || call.conversation_id) && (
              <Section title={t("llm_calls.section_conversation")}>
                <Typography variant="body2" color="text.secondary">
                  {t("llm_calls.related_alone")}
                </Typography>
              </Section>
            )}
          </>
        )}
        {call && tab === "details" && <Details call={call} onFilter={onFilter} />}
        {tab === "json" && (
          <Box sx={{ position: "relative", mt: 2 }}>
            <Button
              size="small"
              startIcon={<ContentCopyIcon />}
              onClick={() => copy(JSON.stringify(current ?? call, null, 2))}
              sx={{ position: "absolute", top: 4, right: 4 }}
            >
              {t("llm_calls.copy")}
            </Button>
            <Box
              component="pre"
              sx={{
                m: 0,
                p: 1.5,
                borderRadius: 1,
                bgcolor: "action.hover",
                fontSize: "0.75rem",
                overflow: "auto",
                fontFamily: "monospace",
              }}
            >
              {JSON.stringify(current ?? call, null, 2)}
            </Box>
          </Box>
        )}
        {!call && !loading && !error && (
          <Typography color="text.secondary" sx={{ mt: 3 }}>
            {t("llm_calls.not_found")}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
