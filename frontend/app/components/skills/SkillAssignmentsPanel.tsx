/**
 * Assignments (administrators): bring a skill into conversations without anyone picking it -- everywhere in
 * the organisation, or in the agents chosen here (sdk/51 F01b). The agents are a searchable set, saved as a
 * whole; nobody types an id. Workspace and project assignments never applied: they are not offered any more,
 * and the old ones are listed under "Not in effect" to be removed.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import FormControlLabel from "@mui/material/FormControlLabel";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import { agentRights, agentsApi, type Agent } from "~/api/agents";
import {
  createAssignment,
  deleteAssignment,
  listAssignments,
  setSkillAgents,
  skillRights,
  type AssignmentTargetType,
  type SkillAssignment,
} from "~/api/skills";
import { AsyncSection } from "~/components/AsyncSection";
import { SectionCard } from "~/components/SectionCard";
import { AgentStatusChip } from "~/components/agents/AgentChips";
import { ADMIN_AGENTS_PATH, agentPath } from "~/components/agents/agentPaths";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";
import { useCan } from "~/lib/useCan";

import { sameIds, sayingAssignmentErrors } from "./assignmentErrors";
import { IdChipsPicker, type ChipLook } from "./IdChipsPicker";

/** Targets no conversation names: assignments to them never applied. */
const NOT_IN_EFFECT: AssignmentTargetType[] = ["workspace", "project"];

export default function SkillAssignmentsPanel({ skillId }: { skillId: string }) {
  const { t } = useTranslation();
  const can = useCan();
  // Only those who administer every skill may bring one into every organisation.
  const everyOrgAllowed = skillRights(can).manage;
  // Those who see every agent can tell a gone agent from one they may not see.
  const seesAllAgents = agentRights(can).seeAll;
  const rows = useAsyncData(() => listAssignments(skillId), [skillId], { initialValue: [] as SkillAssignment[] });
  const agents = useAsyncData(() => agentsApi.list(), [], { initialValue: null as Agent[] | null });
  const change = useAction();
  const save = useAction();

  const assigned = useMemo(
    () => [
      ...new Set(
        rows.data.filter((a) => a.target_type === "assistant" && a.target_id).map((a) => a.target_id as string),
      ),
    ],
    [rows.data],
  );
  const [draft, setDraft] = useState<string[]>(assigned);
  useEffect(() => setDraft(assigned), [assigned]);
  const dirty = !sameIds(draft, assigned);

  const byId = useMemo(() => new Map((agents.data ?? []).map((a) => [a.id, a])), [agents.data]);
  const offered = useMemo(() => (agents.data ?? []).filter((a) => a.status !== "retired").map((a) => a.id), [agents.data]);
  const overrides = new Map(
    rows.data
      .filter((a) => a.target_type === "assistant" && a.priority_override != null)
      .map((a) => [a.target_id, a.priority_override]),
  );
  const ofType = (type: AssignmentTargetType) => rows.data.filter((a) => a.target_type === type);
  const notInEffect = rows.data.filter((a) => NOT_IN_EFFECT.includes(a.target_type));

  const chip = (id: string): ChipLook => {
    const override = overrides.get(id);
    const suffix = override != null ? ` · ${t("skills.priority_override", { value: override })}` : "";
    const agent = byId.get(id);
    if (agent) {
      return {
        label: `${agent.name}${suffix}`,
        to: agentPath(ADMIN_AGENTS_PATH, agent),
        tone: agent.status === "retired" ? "muted" : undefined,
        title: agent.status === "retired" ? t("agents.status.retired") : agent.slug,
      };
    }
    if (agents.data && seesAllAgents) return { label: t("skills.agent_missing"), tone: "warning", title: id };
    return { label: `${id}${suffix}`, title: agents.data ? t("skills.agent_hidden") : undefined };
  };

  /** Switch one of the organisation-wide targets on (an assignment) or off (its rows go). */
  const toggle = (type: "tenant" | "global", on: boolean) =>
    change.run(
      () =>
        sayingAssignmentErrors(t, async () => {
          if (on) await createAssignment(skillId, { target_type: type });
          else for (const row of ofType(type)) await deleteAssignment(skillId, row.id);
        }),
      () => rows.refresh(true),
    );

  const switchRow = (type: "tenant" | "global", label: string, help: string, allowed: boolean) => {
    const found = ofType(type);
    const override = found.find((a) => a.priority_override != null)?.priority_override;
    return (
      <Box>
        <FormControlLabel
          control={
            <Switch
              checked={found.length > 0}
              disabled={!allowed || change.busy || rows.loading}
              onChange={(e) => void toggle(type, e.target.checked)}
            />
          }
          label={label}
        />
        <Typography variant="body2" color="text.secondary" sx={{ ml: 6 }}>
          {help}
          {override != null && ` ${t("skills.priority_override", { value: override })}`}
        </Typography>
      </Box>
    );
  };

  return (
    <Stack spacing={2} sx={{ maxWidth: 760 }}>
      <Typography variant="body2" color="text.secondary">
        {t("skills.assignments_hint")}
      </Typography>
      {change.error && (
        <Alert severity="error" onClose={() => change.setError(null)}>
          {change.error}
        </Alert>
      )}
      <AsyncSection loading={rows.loading} error={rows.error} empty={rows.data.length === 0}>
        <Stack spacing={2}>
          <SectionCard title={t("skills.assign_org_title")}>
            <Stack spacing={1}>
              {switchRow("tenant", t("skills.assign_everyone"), t("skills.assign_everyone_help"), true)}
              {(everyOrgAllowed || ofType("global").length > 0) &&
                switchRow("global", t("skills.assign_every_org"), t("skills.assign_every_org_help"), everyOrgAllowed)}
            </Stack>
          </SectionCard>

          <SectionCard title={t("skills.assign_agents")} description={t("skills.assign_agents_help")}>
            {save.error && <Alert severity="error">{save.error}</Alert>}
            <IdChipsPicker
              value={draft}
              offered={offered}
              onChange={setDraft}
              chip={chip}
              optionText={(id) => byId.get(id)?.name ?? id}
              matches={(id, query) => {
                const agent = byId.get(id);
                return !!agent && (agent.name.toLowerCase().includes(query) || agent.slug.toLowerCase().includes(query));
              }}
              renderOption={(id) => {
                const agent = byId.get(id);
                return (
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                    <span>{agent?.name ?? id}</span>
                    <Typography component="span" variant="body2" color="text.secondary" fontFamily="monospace" noWrap>
                      {agent?.slug}
                    </Typography>
                    {agent && <AgentStatusChip status={agent.status} />}
                  </Stack>
                );
              }}
              label={t("skills.assign_agents")}
              placeholder={t("skills.agents_search")}
              noOptionsText={t("skills.agents_none")}
              helperText={agents.error ? t("skills.agents_unavailable") : undefined}
              disabled={save.busy || !!agents.error}
            />
            {dirty && (
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button size="small" disabled={save.busy} onClick={() => setDraft(assigned)}>
                  {t("common.cancel")}
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  disabled={save.busy}
                  onClick={() =>
                    void save.run(
                      () => sayingAssignmentErrors(t, () => setSkillAgents(skillId, draft)),
                      () => rows.refresh(true),
                    )
                  }
                >
                  {save.busy ? t("common.saving") : t("common.save")}
                </Button>
              </Stack>
            )}
          </SectionCard>

          {notInEffect.length > 0 && (
            <SectionCard title={t("skills.not_in_effect")} description={t("skills.not_in_effect_help")}>
              <List dense disablePadding>
                {notInEffect.map((a) => (
                  <ListItem
                    key={a.id}
                    divider
                    secondaryAction={
                      <Button
                        size="small"
                        color="error"
                        disabled={change.busy}
                        onClick={() =>
                          void change.run(
                            () => deleteAssignment(skillId, a.id),
                            () => rows.refresh(true),
                          )
                        }
                      >
                        {t("common.remove")}
                      </Button>
                    }
                  >
                    <ListItemText
                      primary={`${t(`skills.scope_${a.target_type}`)}${a.target_id ? `: ${a.target_id}` : ""}`}
                      secondary={
                        a.priority_override != null
                          ? t("skills.priority_override", { value: a.priority_override })
                          : undefined
                      }
                    />
                  </ListItem>
                ))}
              </List>
            </SectionCard>
          )}
        </Stack>
      </AsyncSection>
    </Stack>
  );
}
