/**
 * Grants that are running: a person let one agent on one of their devices do,
 * for a while, what would otherwise ask -- one tool ("for a time") or reads
 * and writes in named places ("working alongside"). An administrator may end
 * one; it holds from the agent's next call, a call already running finishes.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { agentsApi, type Agent } from "~/api/agents";
import { ApiError } from "~/api/http";
import { governanceApi, type Grant } from "~/api/governance";
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

export function GrantsTable() {
  const { t } = useTranslation();
  const [agentId, setAgentId] = useState("");
  const [revoking, setRevoking] = useState<Grant | null>(null);
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
      render: (g) => <Chip size="small" variant="outlined" label={t(`governance.grants.kind_value.${g.kind}`)} />,
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
      render: (g) => (g.max_calls === null ? g.calls_used : `${g.calls_used} / ${g.max_calls}`),
      align: "right",
      width: 100,
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
        <Button size="small" variant="outlined" color="error" onClick={() => setRevoking(g)}>
          {t("governance.grants.revoke")}
        </Button>
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
