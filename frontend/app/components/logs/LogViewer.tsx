/**
 * A console for log lines, the same everywhere a log is shown: each line's
 * time, level (coloured), stream and machine, filtered by level, stream,
 * machine and text, following new lines as they come.
 *
 * Lines come from Loki (a deployment, a machine) or from a container's
 * console; ``fromLokiStreams`` and ``fromText`` turn either into lines.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DownloadIcon from "@mui/icons-material/Download";
import SearchIcon from "@mui/icons-material/Search";
import VerticalAlignBottomIcon from "@mui/icons-material/VerticalAlignBottom";

import type { LogStream } from "~/api/logs";

export type LogLevel = "error" | "warning" | "info" | "debug" | "other";
export const LOG_LEVELS: LogLevel[] = ["error", "warning", "info", "debug", "other"];

export interface ViewerLine {
  ts?: string | null;
  level?: string | null;
  message: string;
  /** The machine it was written on. */
  host?: string | null;
  /** Which stream: ``engine``, ``replica``, ``agent``, ``container``... */
  source?: string | null;
}

/** ``(EngineCore pid=13728) (RayWorkerProc pid=8963)`` -- Ray's and vLLM's process prefixes. */
const PREFIX = /^(?:\([A-Za-z_][\w:.-]* pid=\d+(?:, ip=[\d.]+)?\)\s*)+/;
const LEVEL_WORD = /\b(CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE)\b/;
const EXCEPTION = /^(Traceback \(most recent call last\)|[\w.]*(Error|Exception)\b.*:)/;
/** A traceback's frames and the lines between its exceptions. */
const CONTINUES = /^(\s|File "|\^|During handling|The above exception)/;

function named(level: string | null | undefined): LogLevel | null {
  switch ((level ?? "").toLowerCase()) {
    case "error":
    case "err":
    case "critical":
    case "fatal":
      return "error";
    case "warning":
    case "warn":
      return "warning";
    case "info":
    case "notice":
      return "info";
    case "debug":
    case "trace":
      return "debug";
    default:
      return null;
  }
}

/**
 * Each line's level: its own, else what its text says, else -- for a
 * traceback's frames -- the level of the line it belongs to.
 */
export function levelsOf(lines: ViewerLine[]): LogLevel[] {
  let previous: LogLevel = "other";
  return lines.map((line) => {
    const body = line.message.replace(PREFIX, "");
    const level =
      named(line.level) ??
      (EXCEPTION.test(body.trim()) ? "error" : null) ??
      named(body.slice(0, 80).match(LEVEL_WORD)?.[1]) ??
      (CONTINUES.test(body) ? previous : "other");
    previous = level;
    return level;
  });
}

/** Loki streams as lines, oldest first; a Serve replica's JSON line is read for its message and level. */
export function fromLokiStreams(streams: LogStream[]): ViewerLine[] {
  const lines: (ViewerLine & { ms: number })[] = [];
  for (const stream of streams) {
    const labels = stream.labels ?? {};
    const source =
      labels.source ??
      ({ "node-agent": "agent", "node-container": "container", "ray-serve": "replica" } as Record<string, string>)[
        labels.job ?? ""
      ] ??
      labels.job ??
      null;
    for (const entry of stream.entries) {
      let message = entry.line;
      let level: string | null = labels.level && labels.level !== "unknown" ? labels.level : null;
      if (message.trimStart().startsWith("{")) {
        try {
          const doc = JSON.parse(message) as Record<string, unknown>;
          if (typeof doc.message === "string") message = doc.message;
          if (typeof doc.levelname === "string") level = doc.levelname;
        } catch {
          /* not JSON after all: shown as it is */
        }
      }
      const ms = Date.parse(entry.ts);
      lines.push({ ts: entry.ts, level, message, host: labels.host ?? null, source, ms: Number.isNaN(ms) ? 0 : ms });
    }
  }
  return lines.sort((a, b) => a.ms - b.ms);
}

/** A console's text as lines, each keeping a leading timestamp when it has one. */
export function fromText(text: string, source?: string): ViewerLine[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      const stamp = line.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$/);
      return stamp ? { ts: stamp[1], message: stamp[2], source } : { message: line, source };
    });
}

/** Console colours: dark in both themes, as a terminal is. */
const COLOUR: Record<LogLevel, string> = {
  error: "#ff7b72",
  warning: "#e3b341",
  info: "#c9d1d9",
  debug: "#8b949e",
  other: "#c9d1d9",
};
const BADGE: Record<LogLevel, string> = { error: "ERR", warning: "WRN", info: "INF", debug: "DBG", other: "···" };
const CHIP: Record<LogLevel, "error" | "warning" | "info" | "default"> = {
  error: "error",
  warning: "warning",
  info: "info",
  debug: "default",
  other: "default",
};

function clock(ts: string | null | undefined): string {
  if (!ts) return "";
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour12: false });
}

function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i"));
  return <>{parts.map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : part))}</>;
}

/**
 * The console's look, set once on the box: a log is thousands of rows, and
 * a styled component per cell would make each of them a stylesheet lookup.
 */
const CONSOLE_SX = {
  overflow: "auto",
  bgcolor: "#0d1117",
  color: COLOUR.info,
  borderRadius: 1,
  py: 1,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  fontSize: 12,
  lineHeight: 1.55,
  "& .row": { display: "flex", gap: 1.25, px: 1.5, borderLeft: "3px solid transparent" },
  "& .row:hover": { bgcolor: "rgba(110, 118, 129, 0.12)" },
  "& .ts": { color: "#6e7681", flexShrink: 0, minWidth: 64 },
  "& .badge": { flexShrink: 0, fontWeight: 600, width: 28 },
  "& .where": { color: "#8b949e", flexShrink: 0, minWidth: 56 },
  "& .msg": { whiteSpace: "pre-wrap", wordBreak: "break-word", minWidth: 0 },
  "& mark": { bgcolor: "#9e6a03", color: "#fff", borderRadius: "2px" },
  ...Object.fromEntries(
    LOG_LEVELS.map((level) => [`& .row[data-level="${level}"] :is(.badge, .msg)`, { color: COLOUR[level] }]),
  ),
  '& .row[data-level="error"]': { borderLeftColor: COLOUR.error, bgcolor: "rgba(248, 81, 73, 0.08)" },
  '& .row[data-level="warning"]': { borderLeftColor: COLOUR.warning },
};

interface LogViewerProps {
  lines: ViewerLine[];
  loading?: boolean;
  /** Why there is nothing to show, when there is nothing. */
  empty?: string | null;
  sourceLabel?: (source: string) => string;
  hostLabel?: (host: string) => string;
  /** The levels shown; the viewer keeps its own when not given. */
  levels?: LogLevel[];
  onLevelsChange?: (levels: LogLevel[]) => void;
  /** More controls beside the filters (how many lines, fetch). */
  toolbar?: ReactNode;
  /** Pixels, or any CSS length (a dialog gives what is left of the screen). */
  maxHeight?: number | string;
  downloadName?: string;
}

export default function LogViewer({
  lines,
  loading = false,
  empty,
  sourceLabel,
  hostLabel = (host) => host,
  levels: shownLevels,
  onLevelsChange,
  toolbar,
  maxHeight = 480,
  downloadName = "logs.txt",
}: LogViewerProps) {
  const { t } = useTranslation();
  const nameOf = sourceLabel ?? ((source: string) => t(`logviewer.source.${source}`, { defaultValue: source }));
  const [ownLevels, setOwnLevels] = useState<LogLevel[]>(LOG_LEVELS);
  const levels = shownLevels ?? ownLevels;
  const setLevels = onLevelsChange ?? setOwnLevels;
  const [sources, setSources] = useState<string[] | null>(null); // null: all
  const [hosts, setHosts] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const scroller = useRef<HTMLDivElement | null>(null);

  const levelOf = useMemo(() => levelsOf(lines), [lines]);
  const allSources = useMemo(() => [...new Set(lines.map((l) => l.source).filter((s): s is string => !!s))], [lines]);
  const allHosts = useMemo(() => [...new Set(lines.map((l) => l.host).filter((h): h is string => !!h))], [lines]);

  // Everything but the level filter, so the level chips count what they would show.
  const candidates = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return lines
      .map((line, i) => ({ line, level: levelOf[i] }))
      .filter(
        ({ line }) =>
          (!sources || !line.source || sources.includes(line.source)) &&
          (!hosts || !line.host || hosts.includes(line.host)) &&
          (!needle || line.message.toLowerCase().includes(needle)),
      );
  }, [lines, levelOf, sources, hosts, query]);
  const counts = useMemo(() => {
    const n: Record<LogLevel, number> = { error: 0, warning: 0, info: 0, debug: 0, other: 0 };
    for (const { level } of candidates) n[level] += 1;
    return n;
  }, [candidates]);
  const shown = useMemo(() => candidates.filter(({ level }) => levels.includes(level)), [candidates, levels]);

  // Follow new lines, unless the reader scrolled up to read.
  useEffect(() => {
    if (follow && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [shown, follow]);

  function toggle<T>(list: T[] | null, all: T[], value: T): T[] | null {
    const current = list ?? all;
    const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
    return next.length === all.length ? null : next;
  }

  const asText = () =>
    shown
      .map(({ line, level }) =>
        [line.ts ?? "", BADGE[level], line.source ?? "", line.host ?? "", line.message].filter(Boolean).join("  "),
      )
      .join("\n");

  function download() {
    const url = URL.createObjectURL(new Blob([asText()], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = downloadName;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Box data-testid="log-viewer">
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1, flexWrap: "wrap", rowGap: 1 }}>
        {LOG_LEVELS.filter((level) => counts[level] > 0 || level !== "other").map((level) => (
          <Chip
            key={level}
            size="small"
            color={levels.includes(level) ? CHIP[level] : "default"}
            variant={levels.includes(level) ? "filled" : "outlined"}
            label={`${t(`logviewer.level.${level}`)} ${counts[level]}`}
            onClick={() => setLevels(levels.includes(level) ? levels.filter((l) => l !== level) : [...levels, level])}
            data-testid={`log-level-${level}`}
          />
        ))}
        {allSources.length > 1 &&
          allSources.map((source) => (
            <Chip
              key={source}
              size="small"
              variant={!sources || sources.includes(source) ? "filled" : "outlined"}
              label={nameOf(source)}
              onClick={() => setSources(toggle(sources, allSources, source))}
            />
          ))}
        {allHosts.length > 1 &&
          allHosts.map((host) => (
            <Chip
              key={host}
              size="small"
              variant={!hosts || hosts.includes(host) ? "filled" : "outlined"}
              label={hostLabel(host)}
              onClick={() => setHosts(toggle(hosts, allHosts, host))}
            />
          ))}
        <Box sx={{ flexGrow: 1 }} />
        <TextField
          size="small"
          placeholder={t("logviewer.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          sx={{ width: 200 }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            },
          }}
        />
        {toolbar}
        {loading && <CircularProgress size={16} />}
        <Tooltip title={t("logviewer.follow")}>
          <IconButton size="small" color={follow ? "primary" : "default"} onClick={() => setFollow(!follow)}>
            <VerticalAlignBottomIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("logviewer.copy")}>
          <span>
            <IconButton size="small" disabled={!shown.length} onClick={() => void navigator.clipboard.writeText(asText())}>
              <ContentCopyIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title={t("logviewer.download")}>
          <span>
            <IconButton size="small" disabled={!shown.length} onClick={download}>
              <DownloadIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Box
        ref={scroller}
        onScroll={(event) => {
          const box = event.currentTarget;
          const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
          if (atBottom !== follow) setFollow(atBottom);
        }}
        sx={{ ...CONSOLE_SX, maxHeight }}
      >
        {shown.length === 0 ? (
          <Typography variant="body2" sx={{ px: 1.5, color: "#8b949e" }}>
            {lines.length === 0 ? (empty ?? t("logviewer.empty")) : t("logviewer.nothing_matches")}
          </Typography>
        ) : (
          shown.map(({ line, level }, i) => (
            <div key={i} className="row" data-level={level}>
              <span className="ts">{clock(line.ts)}</span>
              <span className="badge">{BADGE[level]}</span>
              {allSources.length > 1 && line.source && <span className="where">{nameOf(line.source)}</span>}
              {allHosts.length > 1 && line.host && <span className="where">{hostLabel(line.host)}</span>}
              <span className="msg">
                <Highlighted text={line.message} query={query.trim()} />
              </span>
            </div>
          ))
        )}
      </Box>
    </Box>
  );
}
