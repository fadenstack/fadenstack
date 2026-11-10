/**
 * Grants that are running: a person let one agent on one of their devices do,
 * for a while, what would otherwise ask -- one tool ("for a time") or reads
 * and writes in named places ("working alongside"). An administrator may end
 * one; it holds from the agent's next call, a call already running finishes.
 * A Bypass grant (approvals off for one conversation) also shows where the
 * device's reports and the gateway's records differ, and who holds the
 * auditor role may read its calls.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { agentsApi, type Agent } from "~/api/agents";
import { ApiError } from "~/api/http";
import { governanceApi, type Grant, type GrantCalls } from "~/api/governance";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { formatDateTime } from "~/lib/dates";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";
import { usePrincipalNames } from "~/lib/usePrincipalNames";

/** The file name of a path; anything else as it is. */
const shortName = (identifier: string) => {
  if (!/[\\/]/.test(identifier)) return identifier;
  const parts = identifier.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? identifier;
};

const places = (g: Grant) => (g.scope.targets ?? []).flatMap((target) => target.identifiers);

/** "Signed in again: password · 10:15": how the person signed in again before a Bypass grant. */
function SignedInAgain({ method, provider, at }: { method: string; provider?: string | null; at?: string | null }) {
  const { t } = useTranslation();
  const values = {
    method: t(`governance.grants.step_up_method.${method}`, { defaultValue: method }),
    provider,
    time: formatDateTime(at),
  };
  return (
    <Typography variant="caption" color="text.secondary" sx={{ display: "block", overflowWrap: "anywhere" }}>
      {provider ? t("governance.grants.signed_in_again_provider", values) : t("governance.grants.signed_in_again", values)}
    </Typography>
  );
}

/** A block of JSON or text that stays small: about ten lines, then it scrolls. */
const scrollBox = {
  fontFamily: "monospace",
  fontSize: "0.75rem",
  m: 0,
  maxHeight: "15em",
  overflow: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
} as const;

/**
 * The calls of one grant, with the places, arguments and results as far as the grant recorded them. Reading is for
 * the auditor role alone, and every read is recorded.
 */
export function GrantCallsDialog({ grantId, onClose }: { grantId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const [calls, setCalls] = useState<GrantCalls | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    governanceApi
      .grantCalls(grantId)
      .then((answer) => live && setCalls(answer))
      .catch((err: unknown) => {
        if (!live) return;
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      live = false;
    };
  }, [grantId]);

  const loading = !calls && !forbidden && !error;
  const empty = calls !== null && calls.data.length === 0 && calls.device_only.length === 0;

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("governance.grants.calls_title")}</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Alert severity="info">{t("governance.grants.calls_note")}</Alert>
          {calls?.reason && (
            <Stack spacing={0.5}>
              <Typography variant="subtitle2">{t("governance.grants.calls_reason")}</Typography>
              <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
                {calls.reason}
              </Typography>
            </Stack>
          )}
          {calls?.step_up && (
            <SignedInAgain method={calls.step_up.method} provider={calls.step_up.provider} at={calls.step_up.at} />
          )}
          {calls?.evidence === "unavailable" && <Alert severity="warning">{t("governance.grants.calls_unavailable")}</Alert>}
          {loading && <CircularProgress size={24} aria-label={t("common.loading")} />}
          {forbidden && <Alert severity="warning">{t("governance.grants.calls_forbidden")}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
          {empty && <Typography variant="body2">{t("governance.grants.calls_empty")}</Typography>}
          {calls && calls.data.length > 0 && (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("governance.grants.calls_col_call")}</TableCell>
                  <TableCell>{t("governance.grants.calls_col_tool")}</TableCell>
                  <TableCell>{t("governance.grants.calls_col_side")}</TableCell>
                  <TableCell>{t("governance.grants.calls_col_time")}</TableCell>
                  <TableCell>{t("governance.grants.calls_col_reported")}</TableCell>
                  <TableCell>{t("governance.grants.calls_col_difference")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {calls.data.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>{c.call_id}</TableCell>
                    <TableCell>
                      <Typography variant="body2" fontFamily="monospace" sx={{ wordBreak: "break-all" }}>
                        {c.tool_id ?? "—"}
                      </Typography>
                      {c.target && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block", overflowWrap: "anywhere" }}
                          aria-label={t("governance.grants.calls_place")}
                        >
                          {c.target}
                        </Typography>
                      )}
                      {c.arguments && (
                        <Box
                          component="pre"
                          sx={scrollBox}
                          aria-label={t("governance.grants.calls_arguments")}
                          tabIndex={0}
                        >
                          {JSON.stringify(c.arguments, null, 2)}
                        </Box>
                      )}
                      {c.outcome && (
                        <Chip
                          size="small"
                          sx={{ mt: 0.5 }}
                          color={c.outcome === "error" ? "error" : "success"}
                          label={t("governance.grants.calls_outcome", {
                            outcome: t(`governance.grants.outcome_${c.outcome}`),
                          })}
                        />
                      )}
                      {c.result && (
                        <Box
                          component="pre"
                          sx={{ ...scrollBox, mt: 0.5 }}
                          aria-label={t("governance.grants.calls_result")}
                          tabIndex={0}
                        >
                          {c.result}
                        </Box>
                      )}
                    </TableCell>
                    <TableCell>{t(`governance.grants.side_${c.side}`)}</TableCell>
                    <TableCell>{formatDateTime(c.created_at)}</TableCell>
                    <TableCell>{c.reported_at ? t("common.yes") : t("common.no")}</TableCell>
                    <TableCell>{c.flag ? t(`governance.grants.flag_${c.flag}`) : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {calls && calls.device_only.length > 0 && (
            <Stack spacing={0.5}>
              <Typography variant="subtitle2">{t("governance.grants.device_only_title")}</Typography>
              {calls.device_only.map((c) => (
                <Typography key={c.call_id} variant="body2">
                  <Box component="span" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
                    {c.call_id}
                  </Box>
                  {" · "}
                  {formatDateTime(c.created_at)}
                </Typography>
              ))}
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.close")}</Button>
      </DialogActions>
    </Dialog>
  );
}

export function GrantsTable() {
  const { t } = useTranslation();
  const [agentId, setAgentId] = useState("");
  const [revoking, setRevoking] = useState<Grant | null>(null);
  const [viewing, setViewing] = useState<Grant | null>(null);
  const [gone, setGone] = useState(false);
  const revoke = useAction();

  const grants = useAsyncData(governanceApi.grants, [], { initialValue: [] as Grant[] });
  const agents = useAsyncData(agentsApi.list, [], { initialValue: [] as Agent[] });
  const agentName = (id: string) => agents.data.find((a) => a.id === id)?.name ?? id;
  const userName = usePrincipalNames(
    grants.data.map((g) => g.user_id),
    agentsApi.namePrincipals,
  );

  const rows = agentId ? grants.data.filter((g) => g.agent_id === agentId) : grants.data;

  const confirmRevoke = async () => {
    const grant = revoking;
    if (!grant) return;
    setGone(false);
    const ok = await revoke.run(async () => {
      try {
        await governanceApi.revokeGrant(grant.id);
      } catch (err) {
        // 404: it ended meanwhile, which is what was asked for.
        if (err instanceof ApiError && err.status === 404) setGone(true);
        else throw err;
      }
    });
    if (ok) {
      setRevoking(null);
      await grants.refresh(true);
    }
  };

  const columns: ColumnDef<Grant>[] = [
    {
      key: "user",
      label: t("governance.grants.user"),
      searchValue: (g) => userName(g.user_id),
      render: (g) => userName(g.user_id),
      width: 200,
    },
    {
      key: "agent",
      label: t("governance.grants.agent"),
      searchValue: (g) => agentName(g.agent_id),
      render: (g) => agentName(g.agent_id),
      width: 160,
    },
    {
      key: "kind",
      label: t("governance.grants.kind"),
      render: (g) => (
        <>
          <Chip size="small" variant="outlined" label={t(`governance.grants.kind_value.${g.kind}`)} />
          {g.step_up_method && (
            <SignedInAgain method={g.step_up_method} provider={g.step_up_provider} at={g.step_up_at} />
          )}
        </>
      ),
      width: 150,
    },
    {
      key: "what",
      label: t("governance.grants.what"),
      searchValue: (g) => [...(g.scope.tools ?? []), ...places(g)].join(" "),
      render: (g) => {
        const classes = (g.scope.classes ?? []).map((c) => t(`governance.grants.classes_value.${c}`, { defaultValue: c }));
        return (
          <>
            {g.kind === "time" ? (
              <Typography variant="body2" fontFamily="monospace" sx={{ overflowWrap: "anywhere" }}>
                {(g.scope.tools ?? []).join(", ") || "—"}
              </Typography>
            ) : (
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                {places(g).map((identifier) => (
                  <Tooltip key={identifier} title={identifier}>
                    <Typography variant="body2" fontFamily="monospace" sx={{ overflowWrap: "anywhere" }}>
                      {shortName(identifier)}
                    </Typography>
                  </Tooltip>
                ))}
              </Stack>
            )}
            {classes.length > 0 && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {classes.join(", ")}
              </Typography>
            )}
          </>
        );
      },
      width: 320,
    },
    {
      key: "left",
      label: t("governance.grants.left"),
      sortable: true,
      sortValue: (g) => g.remaining_s,
      render: (g) => (
        <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" alignItems="center">
          <span>{t("governance.grants.minutes_left", { count: Math.max(0, Math.ceil(g.remaining_s / 60)) })}</span>
          {g.status === "paused" && <Chip size="small" color="warning" label={t("governance.grants.paused")} />}
        </Stack>
      ),
      width: 170,
    },
    {
      key: "calls",
      label: t("governance.grants.calls"),
      sortable: true,
      sortValue: (g) => g.calls_used,
      render: (g) => {
        const used = g.max_calls === null ? g.calls_used : `${g.calls_used} / ${g.max_calls}`;
        if (!g.flags) return used;
        return (
          <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" alignItems="center" justifyContent="flex-end">
            <span>{used}</span>
            <Tooltip title={t("governance.grants.flags_hint")}>
              <Chip size="small" color="warning" label={t("governance.grants.flags", { count: g.flags })} />
            </Tooltip>
          </Stack>
        );
      },
      align: "right",
      width: 200,
    },
    {
      key: "started",
      label: t("governance.grants.started"),
      sortable: true,
      sortValue: (g) => g.issued_at,
      render: (g) => formatDateTime(g.issued_at),
      width: 170,
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (g) => (
        <Stack direction="row" spacing={1} justifyContent="flex-end">
          {g.kind === "bypass" && (
            <Button size="small" variant="outlined" onClick={() => setViewing(g)}>
              {t("governance.grants.show_calls")}
            </Button>
          )}
          <Button size="small" variant="outlined" color="error" onClick={() => setRevoking(g)}>
            {t("governance.grants.revoke")}
          </Button>
        </Stack>
      ),
    },
  ];

  const details = (g: Grant) => {
    const items: [string, string | null][] = [
      [t("governance.grants.device"), g.device_id],
      [t("governance.grants.conversation"), g.conversation_id],
      [t("governance.grants.version"), g.agent_version],
      [t("governance.grants.expires"), formatDateTime(g.expires_at)],
      [t("governance.grants.id"), g.id],
    ];
    return (
      <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 2, rowGap: 0.5, m: 0 }}>
        {items
          .filter(([, value]) => value)
          .map(([name, value]) => (
            <Box key={name} sx={{ display: "contents" }}>
              <Typography component="dt" variant="caption" color="text.secondary">
                {name}
              </Typography>
              <Typography component="dd" variant="caption" fontFamily="monospace" sx={{ m: 0, wordBreak: "break-all" }}>
                {value}
              </Typography>
            </Box>
          ))}
      </Box>
    );
  };

  const all = { value: "", label: t("common.all") };

  return (
    <Stack spacing={1.5}>
      {gone && (
        <Alert severity="info" onClose={() => setGone(false)}>
          {t("governance.grants.gone")}
        </Alert>
      )}
      {revoke.error && (
        <Alert severity="error" onClose={() => revoke.setError(null)}>
          {revoke.error}
        </Alert>
      )}
      <DataTable
        title={t("governance.grants.title")}
        rows={rows}
        columns={columns}
        rowKey={(g) => g.id}
        loading={grants.loading}
        error={grants.error}
        onRefresh={() => void grants.refresh()}
        emptyMessage={t("governance.grants.empty")}
        expansion={details}
        pagination={25}
        columnFilters={[
          {
            label: t("governance.grants.agent"),
            value: agentId,
            onChange: setAgentId,
            options: [all, ...agents.data.map((a) => ({ value: a.id, label: a.name }))],
            minWidth: 160,
          },
        ]}
      />
      {viewing && <GrantCallsDialog grantId={viewing.id} onClose={() => setViewing(null)} />}
      <ConfirmDialog
        open={revoking !== null}
        title={t("governance.grants.revoke_title")}
        message={t("governance.grants.revoke_body", {
          user: revoking ? userName(revoking.user_id) : "",
          agent: revoking ? agentName(revoking.agent_id) : "",
        })}
        confirmLabel={t("governance.grants.revoke")}
        confirmColor="error"
        cancelLabel={t("common.cancel")}
        loading={revoke.busy}
        onConfirm={() => void confirmRevoke()}
        onClose={() => {
          setRevoking(null);
          revoke.setError(null);
        }}
      />
    </Stack>
  );
}
