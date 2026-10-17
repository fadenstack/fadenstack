/**
 * Admin -> Audit log: who did what, from where; filters, a CSV export (itself recorded) and whether the trail is
 * as it was written (its hash chain and file copy).
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { audit, type AuditChain, type AuditEvent, type AuditFilters } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ResultChip, SeverityChip } from "~/components/Chips";
import { useCan } from "~/lib/useCan";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import DownloadIcon from "@mui/icons-material/Download";
import FilterListIcon from "@mui/icons-material/FilterList";

/** `datetime-local` value -> ISO, or nothing. */
function toIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export default function AuditLogsTab() {
  const { t, i18n } = useTranslation();
  const can = useCan();
  const [data, setData] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chain, setChain] = useState<AuditChain | null>(null);
  const [filterAction, setFilterAction] = useState("");
  const [filterTarget, setFilterTarget] = useState("");
  const [filterPerson, setFilterPerson] = useState("");
  const [filterResult, setFilterResult] = useState<"" | "allow" | "deny">("");
  const [filterSeverity, setFilterSeverity] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");

  function filters(): AuditFilters {
    return {
      action: filterAction || undefined,
      target_id: filterTarget || undefined,
      actor_email: filterPerson || undefined,
      result: filterResult || undefined,
      severity: filterSeverity || undefined,
      source: filterSource || undefined,
      since: toIso(filterFrom),
      until: toIso(filterTo),
    };
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [result, verdict] = await Promise.all([
        audit.list({ ...filters(), limit: 200 }),
        audit.chain().catch(() => null),
      ]);
      setData(result);
      setChain(verdict);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("common.load_failed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const columns: ColumnDef<AuditEvent>[] = [
    {
      key: "time",
      label: t("logs.time"),
      sortable: true,
      sortValue: (ev) => new Date(ev.time).getTime(),
      render: (ev) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem" noWrap>
          {new Date(ev.time).toLocaleString(i18n.language || undefined)}
        </Typography>
      ),
    },
    {
      key: "actor",
      label: t("audit.actor"),
      searchValue: (ev) => `${ev.actor_email ?? ""} ${ev.actor_id ?? ""}`,
      render: (ev) => (
        <Typography variant="body2" fontSize="0.8rem" color="text.secondary" noWrap>
          {ev.actor_email ?? ev.actor_id?.slice(0, 8) ?? "—"}
        </Typography>
      ),
    },
    {
      key: "action",
      label: t("audit.action"),
      sortable: true,
      searchValue: (ev) => ev.action,
      render: (ev) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
          {ev.action}
        </Typography>
      ),
    },
    {
      key: "target",
      label: t("audit.target"),
      searchValue: (ev) => ev.target_type + " " + ev.target_id,
      render: (ev) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
          <Box component="span" sx={{ color: "text.disabled", mr: 0.5 }}>
            {ev.target_type}/
          </Box>
          {ev.target_id.slice(0, 48)}
        </Typography>
      ),
    },
    {
      key: "result",
      label: t("audit.result"),
      render: (ev) => <ResultChip value={ev.result} />,
    },
    {
      key: "severity",
      label: t("audit.severity"),
      render: (ev) => <SeverityChip value={ev.severity} />,
    },
    {
      key: "source",
      label: t("audit.source"),
      searchValue: (ev) => `${ev.source} ${ev.client_ip ?? ""}`,
      render: (ev) => (
        <Typography variant="body2" fontSize="0.8rem" color="text.secondary" noWrap>
          {ev.source}
          {ev.client_ip ? ` · ${ev.client_ip}` : ""}
        </Typography>
      ),
    },
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {chain && (
        <Alert severity={chain.ok ? "success" : "error"} sx={{ mb: 1 }}>
          {chain.ok
            ? t("audit.chain_ok", { count: chain.events })
            : t("audit.chain_broken", { problem: chain.problems[0] ?? "" })}
        </Alert>
      )}
      <DataTable
        title={t("audit.title")}
        columns={columns}
        rows={data}
        rowKey={(ev) => ev.id}
        loading={loading}
        error={error}
        emptyMessage={t("audit.empty")}
        onRefresh={load}
        searchPlaceholder={t("audit.search")}
        toolbarActions={
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <TextField
              label={t("audit.person")}
              size="small"
              value={filterPerson}
              onChange={(e) => setFilterPerson(e.target.value)}
              sx={{ width: 180 }}
            />
            <TextField
              label={t("audit.action")}
              size="small"
              value={filterAction}
              onChange={(e) => setFilterAction(e.target.value)}
              sx={{ width: 150 }}
            />
            <TextField
              label={t("audit.target_id")}
              size="small"
              value={filterTarget}
              onChange={(e) => setFilterTarget(e.target.value)}
              sx={{ width: 150 }}
            />
            <TextField
              select
              label={t("audit.result")}
              size="small"
              value={filterResult}
              onChange={(e) => setFilterResult(e.target.value as "" | "allow" | "deny")}
              sx={{ width: 120 }}
            >
              <MenuItem value="">{t("audit.any")}</MenuItem>
              <MenuItem value="allow">{t("audit.result_allow")}</MenuItem>
              <MenuItem value="deny">{t("audit.result_deny")}</MenuItem>
            </TextField>
            <TextField
              select
              label={t("audit.severity")}
              size="small"
              value={filterSeverity}
              onChange={(e) => setFilterSeverity(e.target.value)}
              sx={{ width: 120 }}
            >
              <MenuItem value="">{t("audit.any")}</MenuItem>
              <MenuItem value="high">{t("audit.severity_high")}</MenuItem>
              <MenuItem value="normal">{t("audit.severity_normal")}</MenuItem>
            </TextField>
            <TextField
              select
              label={t("audit.source")}
              size="small"
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
              sx={{ width: 120 }}
            >
              <MenuItem value="">{t("audit.any")}</MenuItem>
              {["console", "api", "cli", "system"].map((source) => (
                <MenuItem key={source} value={source}>
                  {source}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              type="datetime-local"
              label={t("audit.from")}
              size="small"
              value={filterFrom}
              onChange={(e) => setFilterFrom(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <TextField
              type="datetime-local"
              label={t("audit.to")}
              size="small"
              value={filterTo}
              onChange={(e) => setFilterTo(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            <Button variant="outlined" size="small" startIcon={<FilterListIcon />} onClick={load}>
              {t("inference.detail.apply")}
            </Button>
            {can("audit:export") && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<DownloadIcon />}
                component="a"
                href={audit.exportUrl(filters())}
              >
                {t("audit.export")}
              </Button>
            )}
          </Stack>
        }
      />
    </Box>
  );
}
