/**
 * What the checks decided, newest first: the stage, the outcome, what the
 * user was told, and who and which agent it was about. Never the text: a
 * refused prompt or answer is a keyed hash ("the same text again").
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Chip, { type ChipProps } from "@mui/material/Chip";
import Typography from "@mui/material/Typography";

import { agentsApi, type Agent } from "~/api/agents";
import { governanceApi, type DecisionStage, type PolicyDecision } from "~/api/governance";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { formatDateTime } from "~/lib/dates";
import { useAsyncData } from "~/lib/useAsyncData";
import { usePrincipalNames } from "~/lib/usePrincipalNames";

const STAGES: DecisionStage[] = ["input", "output", "tool"];
/** The outcomes the gateway records (docs/governance.md). */
const OUTCOMES = ["prompt_blocked", "response_blocked", "block", "approval_required", "approved", "rejected", "expired"];

const OUTCOME_COLOR: Record<string, ChipProps["color"]> = {
  prompt_blocked: "error",
  response_blocked: "error",
  block: "error",
  approval_required: "warning",
  approved: "success",
  rejected: "warning",
  expired: "default",
};

export function DecisionsTable() {
  const { t } = useTranslation();
  const [stage, setStage] = useState("");
  const [outcome, setOutcome] = useState("");
  const [agentId, setAgentId] = useState("");

  const decisions = useAsyncData(
    () =>
      governanceApi.decisions({
        stage: (stage || undefined) as DecisionStage | undefined,
        outcome: outcome || undefined,
        agent_id: agentId || undefined,
      }),
    [stage, outcome, agentId],
    { initialValue: [] as PolicyDecision[] },
  );
  const agents = useAsyncData(agentsApi.list, [], { initialValue: [] as Agent[] });
  const agentName = (id: string | null) => (id ? (agents.data.find((a) => a.id === id)?.name ?? id) : "—");
  const userName = usePrincipalNames(
    decisions.data.map((d) => d.user_id),
    agentsApi.namePrincipals,
  );

  const label = (key: string, value: string) => t(`${key}.${value}`, { defaultValue: value });

  const columns: ColumnDef<PolicyDecision>[] = [
    {
      key: "when",
      label: t("governance.decisions.when"),
      sortable: true,
      sortValue: (d) => d.created_at,
      render: (d) => formatDateTime(d.created_at),
      width: 170,
    },
    {
      key: "stage",
      label: t("governance.decisions.stage"),
      render: (d) => <Chip size="small" variant="outlined" label={label("governance.decisions.stage_value", d.stage)} />,
      width: 90,
    },
    {
      key: "outcome",
      label: t("governance.decisions.outcome"),
      render: (d) => (
        <Chip size="small" color={OUTCOME_COLOR[d.outcome] ?? "default"} label={label("governance.decisions.outcome_value", d.outcome)} />
      ),
      width: 150,
    },
    {
      key: "message",
      label: t("governance.decisions.message"),
      searchValue: (d) => `${d.message ?? ""} ${d.category ?? ""} ${d.tool_id ?? ""}`,
      render: (d) => (
        <>
          <Typography variant="body2">{d.message ?? "—"}</Typography>
          {(d.category || d.tool_id) && (
            <Typography
              variant="caption"
              color="text.secondary"
              fontFamily={d.tool_id ? "monospace" : undefined}
              sx={{ display: "block", overflowWrap: "anywhere" }}
            >
              {[d.tool_id, d.category].filter(Boolean).join(" · ")}
            </Typography>
          )}
        </>
      ),
      width: 380,
    },
    {
      key: "user",
      label: t("governance.decisions.user"),
      searchValue: (d) => (d.user_id ? userName(d.user_id) : ""),
      render: (d) => (d.user_id ? userName(d.user_id) : "—"),
      width: 210,
    },
    {
      key: "agent",
      label: t("governance.decisions.agent"),
      searchValue: (d) => agentName(d.agent_id),
      render: (d) => agentName(d.agent_id),
      width: 160,
    },
  ];

  const details = (d: PolicyDecision) => {
    const rows: [string, string | null][] = [
      [t("governance.decisions.check"), d.check],
      [t("governance.decisions.policy"), d.policy_id],
      [t("governance.decisions.request"), d.request_id],
      [t("governance.decisions.session"), d.session_id],
      [t("governance.decisions.content_hmac"), d.content_hmac],
    ];
    return (
      <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "max-content 1fr", columnGap: 2, rowGap: 0.5, m: 0 }}>
        {rows
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
    <DataTable
      title={t("governance.decisions.title")}
      rows={decisions.data}
      columns={columns}
      rowKey={(d) => d.id}
      loading={decisions.loading}
      error={decisions.error}
      onRefresh={() => void decisions.refresh()}
      emptyMessage={t("governance.decisions.empty")}
      expansion={details}
      pagination={25}
      columnFilters={[
        {
          label: t("governance.decisions.stage"),
          value: stage,
          onChange: setStage,
          options: [all, ...STAGES.map((s) => ({ value: s, label: label("governance.decisions.stage_value", s) }))],
          minWidth: 120,
        },
        {
          label: t("governance.decisions.outcome"),
          value: outcome,
          onChange: setOutcome,
          options: [all, ...OUTCOMES.map((o) => ({ value: o, label: label("governance.decisions.outcome_value", o) }))],
          minWidth: 170,
        },
        {
          label: t("governance.decisions.agent"),
          value: agentId,
          onChange: setAgentId,
          options: [all, ...agents.data.map((a) => ({ value: a.id, label: a.name }))],
          minWidth: 160,
        },
      ]}
    />
  );
}
