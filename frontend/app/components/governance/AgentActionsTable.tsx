/**
 * Agent actions (with Enterprise), for holders of the role approvals_auditor: every agent action with its approval
 * state, the turn and the model, newest first, filtered by person, agent, device, workspace and time (the last day by
 * default). Each read and each export is recorded. The records say what a person did, so they need a works agreement
 * where one is required.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip, { type ChipProps } from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DownloadIcon from "@mui/icons-material/Download";

import { agentsApi } from "~/api/agents";
import {
  approvalActionsApi,
  type ActionFilter,
  type ActionPage,
  type ActionState,
  type AgentAction,
} from "~/api/approvalActions";
import { ApiError, errorText } from "~/api/http";
import type { PrincipalOption } from "~/api/principals";
import { GrantCallsDialog } from "~/components/governance/GrantsTable";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { formatDateTime } from "~/lib/dates";
import { useAsyncData } from "~/lib/useAsyncData";

const STATE_COLOR: Record<ActionState, ChipProps["color"]> = {
  asked: "warning",
  once: "success",
  chat: "success",
  always: "success",
  approved: "success",
  declined: "warning",
  expired: "default",
  blocked: "error",
  grant: "info",
  bypass: "info",
  auto: "default",
  ran: "default",
};

interface Target {
  id: string;
  label: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A `datetime-local` value as ISO 8601 with the browser's offset; null when empty or no date. */
function withLocalOffset(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const local = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${local}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

export function AgentActionsTable() {
  const { t } = useTranslation();
  const agents = useAsyncData(async () => (await agentsApi.list()).map((a) => ({ id: a.id, label: a.name })), [], {
    initialValue: [] as Target[],
  });

  // What is typed in; it becomes the filter when Search is pressed.
  const [person, setPerson] = useState<Target | null>(null);
  const [agent, setAgent] = useState<Target | null>(null);
  const [device, setDevice] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [filter, setFilter] = useState<ActionFilter>({});

  const [rows, setRows] = useState<AgentAction[]>([]);
  const [next, setNext] = useState<ActionPage["next"]>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    setForbidden(false);
    approvalActionsApi
      .list(filter)
      .then((page) => {
        if (!live) return;
        setRows(page.data);
        setNext(page.next);
        setTruncated(page.places_truncated);
      })
      .catch((err: unknown) => {
        if (!live) return;
        setRows([]);
        setNext(null);
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(errorText(err));
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [filter]);

  const search = () =>
    setFilter({
      person: person?.id ?? null,
      agent: agent?.id ?? null,
      device: device.trim() || null,
      workspace: workspace.trim() || null,
      since: withLocalOffset(since),
      until: withLocalOffset(until),
    });

  const loadMore = useCallback(() => {
    if (!next) return;
    setMore(true);
    setError(null);
    approvalActionsApi
      .list(filter, next)
      .then((page) => {
        setRows((current) => [...current, ...page.data]);
        setNext(page.next);
        setTruncated((was) => was || page.places_truncated);
      })
      .catch((err: unknown) => setError(errorText(err)))
      .finally(() => setMore(false));
  }, [filter, next]);

  return (
    <Stack spacing={2}>
      <Alert severity="info">{t("governance.actions.note")}</Alert>

      <Stack direction={{ xs: "column", md: "row" }} spacing={1} useFlexGap flexWrap="wrap" alignItems={{ md: "flex-start" }}>
        <Box sx={{ minWidth: 220, flex: 1 }}>
          {person ? (
            <Chip label={person.label} onDelete={() => setPerson(null)} />
          ) : (
            <PrincipalPicker
              search={(q) => agentsApi.findPrincipals(q)}
              types={["user"]}
              placeholder={t("governance.actions.person")}
              onPick={(p: PrincipalOption) => setPerson({ id: p.id, label: p.label })}
            />
          )}
        </Box>
        <Autocomplete<Target>
          size="small"
          options={agents.data}
          value={agent}
          getOptionLabel={(a) => a.label}
          isOptionEqualToValue={(a, b) => a.id === b.id}
          onChange={(_, value) => setAgent(value)}
          renderInput={(params) => <TextField {...params} label={t("governance.actions.agent")} />}
          sx={{ minWidth: 200, flex: 1 }}
        />
        <TextField
          size="small"
          label={t("governance.actions.device")}
          value={device}
          onChange={(e) => setDevice(e.target.value)}
          sx={{ minWidth: 160 }}
        />
        <TextField
          size="small"
          label={t("governance.actions.workspace")}
          helperText={t("governance.actions.workspace_help")}
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          sx={{ minWidth: 220, flex: 1 }}
        />
        <TextField
          size="small"
          type="datetime-local"
          label={t("governance.actions.from")}
          value={since}
          onChange={(e) => setSince(e.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          size="small"
          type="datetime-local"
          label={t("governance.actions.to")}
          helperText={t("governance.actions.range_help")}
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <Button variant="contained" onClick={search} disabled={loading}>
          {t("governance.actions.search")}
        </Button>
        <Button
          component="a"
          variant="outlined"
          href={approvalActionsApi.exportUrl(filter)}
          download
          startIcon={<DownloadIcon />}
          disabled={forbidden}
        >
          {t("governance.actions.export")}
        </Button>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}
      {forbidden && <Alert severity="warning">{t("governance.actions.forbidden")}</Alert>}
      {loading && <CircularProgress size={24} aria-label={t("common.loading")} />}
      {!loading && !forbidden && truncated && <Alert severity="info">{t("governance.actions.places_truncated")}</Alert>}
      {!loading && !forbidden && !error && rows.length === 0 && (
        <Typography variant="body2">{t("governance.actions.empty")}</Typography>
      )}

      {!forbidden && rows.length > 0 && (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("governance.actions.col_time")}</TableCell>
                <TableCell>{t("governance.actions.col_person")}</TableCell>
                <TableCell>{t("governance.actions.col_agent")}</TableCell>
                <TableCell>{t("governance.actions.col_state")}</TableCell>
                <TableCell>{t("governance.actions.col_tool")}</TableCell>
                <TableCell>{t("governance.actions.col_source")}</TableCell>
                <TableCell>{t("governance.actions.col_device")}</TableCell>
                <TableCell>{t("governance.actions.col_turn")}</TableCell>
                <TableCell>{t("governance.actions.col_model")}</TableCell>
                <TableCell>{t("governance.actions.col_outcome")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((a) => (
                <TableRow key={a.key}>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{formatDateTime(a.at)}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{a.person ?? a.user_id ?? "—"}</TableCell>
                  <TableCell>
                    {a.agent ?? a.agent_id ?? "—"}
                    {a.agent_version !== null && ` v${a.agent_version}`}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      color={STATE_COLOR[a.state] ?? "default"}
                      label={t(`governance.actions.state_value.${a.state}`, { defaultValue: a.state })}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" fontFamily="monospace" sx={{ overflowWrap: "anywhere" }}>
                      {a.tool ?? "—"}
                    </Typography>
                    {a.place && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block", overflowWrap: "anywhere" }}>
                        {a.place}
                      </Typography>
                    )}
                    {a.grant_id && (
                      <Button size="small" onClick={() => setViewing(a.grant_id)}>
                        {t("governance.actions.show_calls")}
                      </Button>
                    )}
                  </TableCell>
                  <TableCell>{t(`governance.actions.source_value.${a.source}`, { defaultValue: a.source })}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{a.device_id ?? "—"}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>{a.turn_id ?? "—"}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{a.model ?? "—"}</TableCell>
                  <TableCell sx={{ overflowWrap: "anywhere" }}>{a.outcome ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {next && !forbidden && (
        <Box>
          <Button variant="outlined" onClick={loadMore} disabled={more}>
            {t("governance.actions.load_more")}
          </Button>
        </Box>
      )}
      {viewing && <GrantCallsDialog grantId={viewing} onClose={() => setViewing(null)} />}
    </Stack>
  );
}
