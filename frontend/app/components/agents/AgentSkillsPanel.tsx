/**
 * An agent's skills (sdk/51 F01b): the organisation's skills that come into its conversations without anyone
 * picking them -- those assigned to everyone, and those assigned to this agent, which its managers choose here
 * among the skills they administer. Not part of a version: a change applies at once, to every version. The
 * Definition tab's Local skills are something else: people's own skills on their device.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link as RouterLink } from "react-router";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { Agent } from "~/api/agents";
import { agentSkills, listSkills, setAgentSkills, type AgentSkill, type SkillSummary } from "~/api/skills";
import { AsyncSection } from "~/components/AsyncSection";
import { SectionCard } from "~/components/SectionCard";
import { sameIds, sayingAssignmentErrors } from "~/components/skills/assignmentErrors";
import { IdChipsPicker, type ChipLook } from "~/components/skills/IdChipsPicker";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

import { ADMIN_AGENTS_PATH } from "./agentPaths";

/** Most skills the picker offers; the skills API answers at most this many. */
const MAX_OPTIONS = 500;

/** What the panel knows of a skill, from the agent's list or the skills the caller may assign. */
interface SkillInfo {
  name: string;
  slug: string;
  description: string;
  access: string;
}

export interface AgentSkillsPanelProps {
  agent: Agent;
  /** Where the agents pages are: the skill links go to the same side (admin pages, or beside the chat). */
  basePath: string;
}

export function AgentSkillsPanel({ agent, basePath }: AgentSkillsPanelProps) {
  const { t } = useTranslation();
  const assigned = useAsyncData(() => agentSkills(agent.id), [agent.id], { initialValue: [] as AgentSkill[] });
  // The skills the caller may assign: they administer them, and chats get them.
  const assignable = useAsyncData(
    async () =>
      (await listSkills({ view: "all", status: "published", limit: MAX_OPTIONS })).items.filter(
        (s) => s.access === "admin",
      ),
    [],
    { initialValue: [] as SkillSummary[] },
  );
  const save = useAction();

  const everyone = assigned.data.filter((s) => s.via === "everyone");
  const loaded = useMemo(() => assigned.data.filter((s) => s.via === "agent").map((s) => s.id), [assigned.data]);
  const [draft, setDraft] = useState<string[]>(loaded);
  useEffect(() => setDraft(loaded), [loaded]);
  const dirty = !sameIds(draft, loaded);

  const info = useMemo(() => {
    const known = new Map<string, SkillInfo>();
    for (const s of assigned.data) known.set(s.id, { name: s.name, slug: s.slug, description: "", access: s.access });
    for (const s of assignable.data) known.set(s.id, { name: s.name, slug: s.slug, description: s.description, access: s.access });
    return known;
  }, [assigned.data, assignable.data]);
  const offered = useMemo(() => assignable.data.map((s) => s.id), [assignable.data]);
  const mayChange = (id: string) => info.get(id)?.access === "admin";
  const skillPath = (id: string) => `${basePath === ADMIN_AGENTS_PATH ? "/admin/skills" : "/skills"}/${encodeURIComponent(id)}`;

  const chip = (id: string): ChipLook => {
    const skill = info.get(id);
    return {
      label: skill?.name ?? id,
      to: skillPath(id),
      fixed: !mayChange(id),
      title: mayChange(id) ? skill?.slug : t("agents.skills.not_yours"),
    };
  };

  return (
    <Stack spacing={2} sx={{ maxWidth: 760 }}>
      <Typography variant="body2" color="text.secondary">
        {t("agents.skills.help")}
      </Typography>
      <AsyncSection loading={assigned.loading} error={assigned.error} empty={assigned.data.length === 0}>
        <Stack spacing={2}>
          <SectionCard title={t("agents.skills.everyone")} description={t("agents.skills.everyone_help")}>
            {everyone.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                {t("agents.skills.everyone_none")}
              </Typography>
            ) : (
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {everyone.map((s) => (
                  <Chip
                    key={s.id}
                    size="small"
                    label={s.name}
                    component={RouterLink}
                    to={skillPath(s.id)}
                    clickable
                    variant={s.status === "published" ? "filled" : "outlined"}
                    title={s.status === "published" ? s.slug : t("agents.skills.not_live")}
                  />
                ))}
              </Stack>
            )}
          </SectionCard>

          <SectionCard title={t("agents.skills.this_agent")} description={t("agents.skills.this_agent_help")}>
            {save.error && <Alert severity="error">{save.error}</Alert>}
            {assignable.error && <Alert severity="warning">{assignable.error}</Alert>}
            <IdChipsPicker
              value={draft}
              offered={offered}
              onChange={setDraft}
              chip={chip}
              optionText={(id) => info.get(id)?.name ?? id}
              matches={(id, query) => {
                const skill = info.get(id);
                return (
                  !!skill &&
                  [skill.name, skill.slug, skill.description].some((text) => text.toLowerCase().includes(query))
                );
              }}
              renderOption={(id) => {
                const skill = info.get(id);
                return (
                  <Stack sx={{ minWidth: 0 }}>
                    <span>{skill?.name ?? id}</span>
                    <Typography variant="body2" color="text.secondary" noWrap>
                      {skill?.description}
                    </Typography>
                  </Stack>
                );
              }}
              label={t("agents.skills.this_agent")}
              placeholder={t("agents.skills.search")}
              noOptionsText={t(offered.length ? "agents.skills.no_match" : "agents.skills.nothing_to_assign")}
              disabled={save.busy}
            />
            {dirty && (
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button size="small" disabled={save.busy} onClick={() => setDraft(loaded)}>
                  {t("common.cancel")}
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  disabled={save.busy}
                  onClick={() =>
                    void save.run(
                      // Only the skills the caller administers: the others' assignments stay as they are.
                      () => sayingAssignmentErrors(t, () => setAgentSkills(agent.id, draft.filter(mayChange))),
                      () => assigned.refresh(true),
                    )
                  }
                >
                  {save.busy ? t("common.saving") : t("common.save")}
                </Button>
              </Stack>
            )}
          </SectionCard>
        </Stack>
      </AsyncSection>
    </Stack>
  );
}
