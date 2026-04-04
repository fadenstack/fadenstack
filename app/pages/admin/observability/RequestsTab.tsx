/**
 * Requests tab — paginated log of gateway requests with cost detail.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  observability,
  type PaginatedRequests,
  type RequestLog,
} from "~/api/observability";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import Pagination from "@mui/material/Pagination";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";

function statusColor(code: number) {
  if (code >= 200 && code < 300) return "success" as const;
  if (code >= 400 && code < 500) return "warning" as const;
  return "error" as const;
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString();
}

function fmtCost(v: number | string | null | undefined): string {
  if (v == null) return "—";
  return `$${Number(v).toFixed(6)}`;
}

interface Props {
  start: string;
  end: string;
}

export default function RequestsTab({ start, end }: Props) {
  const { t } = useTranslation();
  const [data, setData] = useState<PaginatedRequests | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [modelFilter, setModelFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await observability.requests(start, end, {
        page,
        limit: 50,
        model_alias: modelFilter || undefined,
        user_id: userFilter || undefined,
      });
      setData(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load requests");
    } finally {
      setLoading(false);
    }
  }, [start, end, page, modelFilter, userFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = data ? Math.ceil(data.total / data.limit) : 0;

  return (
    <Box>
      {/* Filters */}
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <TextField
          size="small"
          label={t("observability.filter_model")}
          value={modelFilter}
          onChange={(e) => {
            setModelFilter(e.target.value);
            setPage(1);
          }}
          sx={{ width: 200 }}
        />
        <TextField
          size="small"
          label={t("observability.filter_user_id")}
          value={userFilter}
          onChange={(e) => {
            setUserFilter(e.target.value);
            setPage(1);
          }}
          sx={{ width: 200 }}
        />
        <Button variant="outlined" size="small" onClick={load}>
          {t("observability.search")}
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress />
        </Box>
      ) : !data || data.items.length === 0 ? (
        <Typography color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          {t("observability.no_requests")}
        </Typography>
      ) : (
        <>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell />
                  <TableCell>{t("observability.col_time")}</TableCell>
                  <TableCell>{t("observability.col_model")}</TableCell>
                  <TableCell>{t("observability.col_status")}</TableCell>
                  <TableCell align="right">
                    {t("observability.col_latency")}
                  </TableCell>
                  <TableCell align="right">
                    {t("observability.col_tokens")}
                  </TableCell>
                  <TableCell align="right">
                    {t("observability.col_est_cost")}
                  </TableCell>
                  <TableCell>{t("observability.col_user")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.items.map((row) => (
                  <RequestRow
                    key={row.id}
                    row={row}
                    expanded={expanded === row.id}
                    onToggle={() =>
                      setExpanded(expanded === row.id ? null : row.id)
                    }
                  />
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          {totalPages > 1 && (
            <Stack direction="row" justifyContent="center" sx={{ mt: 2 }}>
              <Pagination
                count={totalPages}
                page={page}
                onChange={(_, v) => setPage(v)}
                size="small"
              />
            </Stack>
          )}

          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 1, display: "block" }}
          >
            {t("observability.total_requests_count", { count: data.total })}
          </Typography>
        </>
      )}
    </Box>
  );
}

// ── Request row with expandable detail ──────────────────────────────────────

function RequestRow({
  row,
  expanded,
  onToggle,
}: {
  row: RequestLog;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <TableRow hover sx={{ cursor: "pointer" }} onClick={onToggle}>
        <TableCell sx={{ width: 32, p: 0.5 }}>
          <IconButton size="small">
            {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          </IconButton>
        </TableCell>
        <TableCell sx={{ whiteSpace: "nowrap" }}>
          {fmtDate(row.created_at)}
        </TableCell>
        <TableCell>{row.model_alias ?? "—"}</TableCell>
        <TableCell>
          <Chip
            label={row.status_code}
            size="small"
            color={statusColor(row.status_code)}
          />
        </TableCell>
        <TableCell align="right">{row.latency_ms} ms</TableCell>
        <TableCell align="right">{row.total_tokens ?? "—"}</TableCell>
        <TableCell align="right">{fmtCost(row.estimated_total_cost)}</TableCell>
        <TableCell
          sx={{ maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }}
        >
          {row.user_id}
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell
          colSpan={8}
          sx={{ p: 0, borderBottom: expanded ? undefined : "none" }}
        >
          <Collapse in={expanded} unmountOnExit>
            <Box sx={{ p: 2, bgcolor: "action.hover" }}>
              <Stack direction="row" spacing={4} flexWrap="wrap">
                <Detail
                  label={t("observability.request_id")}
                  value={row.request_id}
                />
                <Detail
                  label={t("observability.trace_id")}
                  value={row.trace_id ?? "—"}
                />
                <Detail
                  label={t("observability.provider")}
                  value={row.provider_instance_id ?? "—"}
                />
                <Detail
                  label={t("observability.endpoint")}
                  value={row.endpoint}
                />
                <Detail
                  label={t("observability.stream")}
                  value={row.stream != null ? String(row.stream) : "—"}
                />
                <Detail
                  label={t("observability.ttft")}
                  value={row.ttft_ms != null ? `${row.ttft_ms} ms` : "—"}
                />
                <Detail
                  label={t("observability.prompt_tokens")}
                  value={String(row.prompt_tokens ?? "—")}
                />
                <Detail
                  label={t("observability.completion_tokens")}
                  value={String(row.completion_tokens ?? "—")}
                />
                <Detail
                  label={t("observability.cached_tokens")}
                  value={String(row.cached_tokens ?? "—")}
                />
                <Detail
                  label={t("observability.input_cost")}
                  value={fmtCost(row.estimated_input_cost)}
                />
                <Detail
                  label={t("observability.output_cost")}
                  value={fmtCost(row.estimated_output_cost)}
                />
                <Detail
                  label={t("observability.total_cost")}
                  value={fmtCost(row.estimated_total_cost)}
                />
                <Detail
                  label={t("observability.currency")}
                  value={row.currency ?? "—"}
                />
                <Detail
                  label={t("observability.estimate_status")}
                  value={row.cost_estimate_status ?? "—"}
                />
                {row.error_code && (
                  <Detail
                    label={t("observability.error")}
                    value={row.error_code}
                  />
                )}
              </Stack>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2">{value}</Typography>
    </Box>
  );
}
