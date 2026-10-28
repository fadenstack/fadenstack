/**
 * LLM Calls — every call through the gateway, to find the one that went wrong and see why.
 *
 * Modelled on how trace tools (Langfuse and the like) are used for debugging:
 * filter the list down, read the numbers, open a call beside the list, look at
 * where its time went and what else happened in its conversation. Everything
 * the page knows is in the URL, so a link to a broken call is a bug report.
 *
 * Metadata only: the gateway keeps no prompts or completions. Enterprise
 * installs with the full Langfuse see content there.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Drawer from "@mui/material/Drawer";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import TablePagination from "@mui/material/TablePagination";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { keyframes, useTheme } from "@mui/material/styles";
import PauseIcon from "@mui/icons-material/Pause";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import SearchIcon from "@mui/icons-material/Search";

import {
  llmCalls,
  type CallOutcome,
  type CallQuery,
  type CallSort,
  type LlmCall,
  type LlmCallPage,
  type LlmCallStats,
  type LlmRoute,
  type LlmTurnPage,
} from "~/api/llmCalls";
import { RangeToggle } from "~/components/dashboard/kit";

import CallDetail from "./calls/CallDetail";
import CallsTable from "./calls/CallsTable";
import TurnsTable from "./calls/TurnsTable";
import RoutingMap from "./calls/RoutingMap";
import StatsStrip from "./calls/StatsStrip";
import { shortId } from "./calls/format";

const RANGES = { "15m": 15 * 60_000, "1h": 3_600_000, "6h": 6 * 3_600_000, "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000 } as const;
type Range = keyof typeof RANGES;
const DEFAULT_RANGE: Range = "24h";
const LIVE_REFRESH_MS = 5000;
const PAGE_SIZES = [25, 50, 100, 200];

const pulse = keyframes`
  0% { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.6); }
  70% { box-shadow: 0 0 0 6px rgba(76, 175, 80, 0); }
  100% { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0); }
`;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(handle);
  }, [value, ms]);
  return debounced;
}

export default function LlmCallsPage() {
  const { t } = useTranslation();
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("lg"));
  const [params, setParams] = useSearchParams();

  const range = (params.get("range") as Range | null) ?? DEFAULT_RANGE;
  const rangeMs = RANGES[range] ?? RANGES[DEFAULT_RANGE];
  const q = params.get("q") ?? "";
  const model = params.get("model") ?? "";
  const outcome = (params.get("outcome") as CallOutcome | null) ?? undefined;
  const session = params.get("session") ?? "";
  const sort = (params.get("sort") as CallSort | null) ?? "newest";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const limit = PAGE_SIZES.includes(Number(params.get("limit"))) ? Number(params.get("limit")) : 50;
  const selectedId = params.get("call");
  const view = params.get("view") === "routing" ? "routing" : "calls";
  /** Requests (an agent's rounds for one user request, together) unless each call is asked for. */
  const grouped = params.get("group") !== "calls";

  const update = useCallback(
    (changes: Record<string, string | null | undefined>, opts: { resetPage?: boolean; push?: boolean } = {}) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(changes)) {
            if (value === null || value === undefined || value === "") next.delete(key);
            else next.set(key, value);
          }
          if (opts.resetPage) next.delete("page");
          return next;
        },
        { replace: !opts.push },
      );
    },
    [setParams],
  );

  const [search, setSearch] = useState(q);
  const debouncedSearch = useDebounced(search, 350);
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    if (debouncedSearch.trim() !== q) update({ q: debouncedSearch.trim() }, { resetPage: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const [live, setLive] = useState(true);
  const [data, setData] = useState<LlmCallPage | null>(null);
  const [turnData, setTurnData] = useState<LlmTurnPage | null>(null);
  const [stats, setStats] = useState<LlmCallStats | null>(null);
  const [routes, setRoutes] = useState<LlmRoute[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const knownIds = useRef<Set<string>>(new Set());
  const queryKey = JSON.stringify([rangeMs, q, model, outcome, session, sort, page, limit, view, grouped]);
  const lastKey = useRef<string>("");
  /** Bumped by every load: only the latest one may write, whatever its trigger (filter, poll, button). */
  const loadSeq = useRef(0);

  const load = useCallback(
    async (signal: AbortSignal, background: boolean) => {
      const end = new Date();
      const query: CallQuery = {
        start: new Date(end.getTime() - rangeMs).toISOString(),
        end: end.toISOString(),
        q: q || undefined,
        model_alias: model || undefined,
        outcome,
        session: session || undefined,
      };
      const seq = ++loadSeq.current;
      const stale = () => signal.aborted || seq !== loadSeq.current;
      if (!background) setLoading(true);
      try {
        const [list, summary, routing, turnList] = await Promise.all([
          view === "calls" && !grouped ? llmCalls.list(query, { page, limit, sort }, signal) : Promise.resolve(null),
          llmCalls.stats(query, signal),
          view === "routing" ? llmCalls.routing(query, signal) : Promise.resolve(null),
          view === "calls" && grouped ? llmCalls.turns(query, { page, limit, sort }, signal) : Promise.resolve(null),
        ]);
        if (stale()) return;
        if (list) {
          // A refresh marks what is new; a new filter just shows its calls.
          const ids = list.items.map((c) => c.id);
          setFresh(background ? new Set(ids.filter((id) => !knownIds.current.has(id))) : new Set());
          knownIds.current = new Set(ids);
          setData(list);
        }
        if (turnList) {
          // A turn that got another round counts as new too.
          const keys = turnList.items.map((x) => `${x.key}:${x.calls}`);
          setFresh(background ? new Set(turnList.items.filter((x, i) => !knownIds.current.has(keys[i])).map((x) => x.key)) : new Set());
          knownIds.current = new Set(keys);
          setTurnData(turnList);
        }
        if (routing) setRoutes(routing.routes);
        setStats(summary);
        setError(null);
        setNow(Date.now());
        setLastUpdated(Date.now());
      } catch (e: unknown) {
        if (stale()) return;
        if (!background) setError(e instanceof Error ? e.message : t("llm_calls.failed_load"));
      } finally {
        if (!stale()) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [queryKey, t],
  );

  useEffect(() => {
    const controller = new AbortController();
    const background = lastKey.current === queryKey;
    lastKey.current = queryKey;
    void load(controller.signal, background);
    return () => controller.abort();
  }, [load, queryKey]);

  // Live: newer calls come in on their own while on the first page of the newest.
  const following = live && page === 1 && (sort === "newest" || view === "routing");
  useEffect(() => {
    if (!following) return;
    let controller: AbortController | null = null;
    const handle = window.setInterval(() => {
      if (document.hidden) return;
      controller?.abort();
      controller = new AbortController();
      void load(controller.signal, true);
    }, LIVE_REFRESH_MS);
    return () => {
      window.clearInterval(handle);
      controller?.abort();
    };
  }, [following, load]);

  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(handle);
  }, []);

  const calls = data?.items ?? [];
  const turns = turnData?.items ?? [];
  const selectedIndex = selectedId && !grouped ? calls.findIndex((c) => c.id === selectedId) : -1;
  const turnIndex = selectedId && grouped ? turns.findIndex((x) => x.last_call_id === selectedId) : -1;
  // A linked call may be on another page of the list: the map fetches it on its own to draw its way.
  const [linkedCall, setLinkedCall] = useState<LlmCall | null>(null);
  const inList = selectedIndex >= 0;
  useEffect(() => {
    if (!selectedId || inList || view !== "routing") {
      setLinkedCall(null);
      return;
    }
    const controller = new AbortController();
    llmCalls
      .detail(selectedId, controller.signal)
      .then((d) => setLinkedCall(d.call))
      .catch(() => setLinkedCall(null));
    return () => controller.abort();
  }, [selectedId, inList, view]);
  const selectedCall: LlmCall | null = inList ? calls[selectedIndex] : linkedCall;
  const select = useCallback((id: string | null) => update({ call: id }), [update]);
  const navIds = grouped ? turns.map((x) => x.last_call_id) : calls.map((c) => c.id);
  const navIndex = grouped ? turnIndex : selectedIndex;
  const onPrev = navIndex > 0 ? () => select(navIds[navIndex - 1]) : undefined;
  const onNext = navIndex >= 0 && navIndex < navIds.length - 1 ? () => select(navIds[navIndex + 1]) : undefined;

  const listTotal = grouped ? (turnData?.total ?? null) : (data?.total ?? null);

  const modelOptions = useMemo(() => {
    const list = (stats?.models ?? []).map((m) => m.model_alias);
    if (model && !list.includes(model)) list.unshift(model);
    return list;
  }, [stats, model]);

  const filterChips = [
    session && { key: "session", label: t("llm_calls.chip_session", { id: shortId(session) }) },
  ].filter(Boolean) as Array<{ key: string; label: string }>;

  const detail = selectedId ? (
    <CallDetail
      callId={selectedId}
      preview={selectedCall}
      p95LatencyMs={stats?.p95_latency_ms ?? null}
      onClose={() => select(null)}
      onSelect={(id) => select(id)}
      onPrev={onPrev}
      onNext={onNext}
      onFilter={(key, value) =>
        update({ [key === "model_alias" ? "model" : "session"]: value, view: null }, { resetPage: true, push: true })
      }
    />
  ) : null;

  return (
    <Box
      data-keeps-height
      sx={{ display: "flex", flexDirection: "column", gap: 1.5, height: "100%", minHeight: 0 }}
    >
      {/* Filters */}
      <Paper sx={{ p: 1.25, display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <TextField
          size="small"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("llm_calls.search_placeholder")}
          sx={{ flex: "1 1 280px", minWidth: 220 }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            },
            htmlInput: { "aria-label": t("llm_calls.search") },
          }}
        />
        <FormControl size="small" sx={{ minWidth: 180 }}>
          <Select
            value={model}
            displayEmpty
            onChange={(e) => update({ model: e.target.value }, { resetPage: true })}
            inputProps={{ "aria-label": t("llm_calls.col_model") }}
            renderValue={(v) => (v ? v : <Typography color="text.secondary" variant="body2" component="span">{t("llm_calls.all_models")}</Typography>)}
          >
            <MenuItem value="">{t("llm_calls.all_models")}</MenuItem>
            {modelOptions.map((m) => (
              <MenuItem key={m} value={m}>
                {m}
                <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                  {stats?.models.find((x) => x.model_alias === m)?.calls ?? ""}
                </Typography>
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={outcome ?? "all"}
          onChange={(_, v: string | null) => v && update({ outcome: v === "all" ? null : v }, { resetPage: true })}
          aria-label={t("llm_calls.outcome")}
        >
          <ToggleButton value="all" sx={{ px: 1.5, textTransform: "none" }}>
            {t("llm_calls.outcome_all")}
          </ToggleButton>
          <ToggleButton value="error" sx={{ px: 1.5, textTransform: "none" }}>
            {t("llm_calls.outcome_error")}
          </ToggleButton>
          <ToggleButton value="ok" sx={{ px: 1.5, textTransform: "none" }}>
            {t("llm_calls.outcome_ok")}
          </ToggleButton>
        </ToggleButtonGroup>
        <RangeToggle<Range>
          value={range}
          options={(Object.keys(RANGES) as Range[]).map((r) => ({ value: r, label: t(`llm_calls.range_${r}`) }))}
          onChange={(r) => update({ range: r === DEFAULT_RANGE ? null : r }, { resetPage: true })}
          ariaLabel={t("llm_calls.range")}
        />
        <Box sx={{ flex: 1 }} />
        <Tooltip title={following ? t("llm_calls.live_on") : page > 1 || sort !== "newest" ? t("llm_calls.live_paused_page") : t("llm_calls.live_off")}>
          <Button
            size="small"
            variant={live ? "outlined" : "text"}
            color={following ? "success" : "inherit"}
            onClick={() => setLive((v) => !v)}
            startIcon={live ? <PauseIcon /> : <PlayArrowIcon />}
            sx={{ textTransform: "none" }}
          >
            <Box
              component="span"
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                mr: 0.75,
                bgcolor: following ? "success.main" : "text.disabled",
                animation: following ? `${pulse} 2s infinite` : "none",
              }}
            />
            {t("llm_calls.live")}
          </Button>
        </Tooltip>
        <Tooltip title={lastUpdated ? t("llm_calls.updated_at", { time: new Date(lastUpdated).toLocaleTimeString() }) : ""}>
          <IconButton
            size="small"
            aria-label={t("llm_calls.refresh")}
            onClick={() => {
              const controller = new AbortController();
              void load(controller.signal, false);
            }}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {filterChips.length > 0 && (
          <Stack direction="row" spacing={1} sx={{ flexBasis: "100%" }}>
            {filterChips.map((c) => (
              <Chip key={c.key} size="small" label={c.label} onDelete={() => update({ [c.key]: null }, { resetPage: true })} />
            ))}
            <Button size="small" onClick={() => update({ session: null, user: null, q: null, model: null, outcome: null }, { resetPage: true })}>
              {t("llm_calls.clear_filters")}
            </Button>
          </Stack>
        )}
      </Paper>

      <StatsStrip
        stats={stats}
        loading={loading}
        errorsOnly={outcome === "error"}
        onToggleErrors={() => update({ outcome: outcome === "error" ? null : "error" }, { resetPage: true })}
      />

      {error && <Alert severity="error">{error}</Alert>}

      {/* Calls / routing */}
      <Paper sx={{ flex: 1, minHeight: 360, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <Stack direction="row" alignItems="center" sx={{ borderBottom: 1, borderColor: "divider", pr: 1 }}>
          <Tabs
            value={view}
            onChange={(_, v) => update({ view: v === "calls" ? null : v })}
            sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, textTransform: "none" } }}
          >
            <Tab value="calls" label={t("llm_calls.view_calls")} />
            <Tab value="routing" label={t("llm_calls.view_routing")} />
          </Tabs>
          <Box sx={{ flex: 1 }} />
          {view === "calls" && (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={grouped ? "turns" : "calls"}
              onChange={(_, v: string | null) => v && update({ group: v === "turns" ? null : v, call: null }, { resetPage: true })}
              aria-label={t("llm_calls.group_by")}
              sx={{ mr: 1.5, "& .MuiToggleButton-root": { py: 0.25, px: 1.25, textTransform: "none" } }}
            >
              <ToggleButton value="turns" title={t("llm_calls.group_turns_tooltip")}>
                {t("llm_calls.group_turns")}
              </ToggleButton>
              <ToggleButton value="calls" title={t("llm_calls.group_calls_tooltip")}>
                {t("llm_calls.group_calls")}
              </ToggleButton>
            </ToggleButtonGroup>
          )}
          {view === "calls" && listTotal !== null && (
            <Typography variant="caption" color="text.secondary">
              {grouped ? t("llm_calls.showing_turns", { count: listTotal }) : t("llm_calls.showing", { count: listTotal })}
            </Typography>
          )}
        </Stack>
        <Box sx={{ height: 2 }}>{loading && <LinearProgress sx={{ height: 2 }} />}</Box>

        {view === "routing" ? (
          <Box sx={{ flex: 1, minHeight: 0 }}>
            {routes && (
              <RoutingMap
                routes={routes}
                selected={selectedCall}
                now={now}
                onFilterModel={(m) => update({ model: m, view: null }, { resetPage: true, push: true })}
              />
            )}
          </Box>
        ) : (
          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              display: "grid",
              gridTemplateColumns: selectedId && !narrow ? "minmax(0, 1fr) minmax(420px, 46%)" : "minmax(0, 1fr)",
            }}
          >
            <Box sx={{ display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
              <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
                {listTotal === 0 && !loading ? (
                  <Box sx={{ p: 6, textAlign: "center" }}>
                    <Typography variant="subtitle1">{t("llm_calls.empty_title")}</Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                      {t("llm_calls.empty_hint")}
                    </Typography>
                  </Box>
                ) : grouped ? (
                  <TurnsTable
                    turns={turns}
                    selectedId={selectedId}
                    onSelect={(id) => select(id === selectedId ? null : id)}
                    p95LatencyMs={stats?.p95_latency_ms ?? null}
                    sort={sort}
                    onSort={(s) => update({ sort: s === "newest" ? null : s }, { resetPage: true })}
                    fresh={fresh}
                    now={now}
                    compact={Boolean(selectedId) && !narrow}
                  />
                ) : (
                  <CallsTable
                    calls={calls}
                    selectedId={selectedId}
                    onSelect={(id) => select(id === selectedId ? null : id)}
                    p95LatencyMs={stats?.p95_latency_ms ?? null}
                    sort={sort}
                    onSort={(s) => update({ sort: s === "newest" ? null : s }, { resetPage: true })}
                    fresh={fresh}
                    now={now}
                    compact={Boolean(selectedId) && !narrow}
                  />
                )}
              </Box>
              {listTotal !== null && listTotal > 0 && (
                <TablePagination
                  component="div"
                  count={listTotal}
                  page={page - 1}
                  rowsPerPage={limit}
                  rowsPerPageOptions={PAGE_SIZES}
                  onPageChange={(_, p) => update({ page: p > 0 ? String(p + 1) : null })}
                  onRowsPerPageChange={(e) => update({ limit: e.target.value === "50" ? null : e.target.value }, { resetPage: true })}
                  labelRowsPerPage={t("llm_calls.rows_per_page")}
                  sx={{ borderTop: 1, borderColor: "divider", flexShrink: 0 }}
                />
              )}
            </Box>
            {selectedId && !narrow && (
              <Box sx={{ borderLeft: 1, borderColor: "divider", minHeight: 0, minWidth: 0 }}>{detail}</Box>
            )}
          </Box>
        )}
      </Paper>

      {narrow && (
        <Drawer
          anchor="right"
          open={Boolean(selectedId) && view === "calls"}
          onClose={() => select(null)}
          slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 } } } }}
        >
          {detail}
        </Drawer>
      )}
    </Box>
  );
}
