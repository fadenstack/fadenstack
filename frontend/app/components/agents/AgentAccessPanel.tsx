/**
 * Who may use an agent (users and teams), who looks after it (maintainers),
 * and why a particular person may or may not use it.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";

import { agentsApi, type AccessExplanation, type Agent, type AgentGrant, type AgentMember } from "~/api/agents";
import { principalKey, type PrincipalOption } from "~/api/principals";
import { AsyncSection } from "~/components/AsyncSection";
import { PrincipalList, samePrincipals } from "~/components/PrincipalList";
import { PrincipalPicker } from "~/components/PrincipalPicker";
import { SectionCard } from "~/components/SectionCard";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";

/** A maintainer as a list row; the role is kept as the server has it. */
type MemberRow = PrincipalOption & { role: string };

const NEW_MEMBER_ROLE = "maintainer";

const grantRow = (g: AgentGrant): PrincipalOption => ({
  type: g.principal_type,
  id: g.principal_id,
  label: g.label ?? g.principal_id,
});

const memberRow = (m: AgentMember): MemberRow => ({
  type: "user",
  id: m.user_id,
  label: m.label ?? m.user_id,
  role: m.role,
});

const keysOf = (rows: PrincipalOption[]) => new Set(rows.map((r) => principalKey(r.type, r.id)));

/**
 * A list edited in place and saved as a whole: the rows on screen, whether
 * they differ from what was loaded, and the save.
 */
function useListDraft<T extends PrincipalOption>(loaded: T[]) {
  const [rows, setRows] = useState<T[]>(loaded);
  useEffect(() => setRows(loaded), [loaded]);
  return {
    rows,
    taken: useMemo(() => keysOf(rows), [rows]),
    dirty: !samePrincipals(rows, loaded),
    add: (row: T) => setRows((prev) => [...prev, row]),
    remove: (row: T) => setRows((prev) => prev.filter((r) => principalKey(r.type, r.id) !== principalKey(row.type, row.id))),
    reset: () => setRows(loaded),
  };
}

export interface AgentAccessPanelProps {
  agent: Agent;
}

export function AgentAccessPanel({ agent }: AgentAccessPanelProps) {
  const { t } = useTranslation();
  const editable = agent.can_manage;

  const grants = useAsyncData(async () => (await agentsApi.grants(agent.slug)).map(grantRow), [agent.slug], {
    initialValue: [] as PrincipalOption[],
  });
  const members = useAsyncData(async () => (await agentsApi.members(agent.slug)).map(memberRow), [agent.slug], {
    initialValue: [] as MemberRow[],
  });

  const grantDraft = useListDraft(grants.data);
  const memberDraft = useListDraft(members.data);
  const saveGrants = useAction();
  const saveMembers = useAction();

  const search = (q: string) => agentsApi.findPrincipals(q);

  return (
    <Stack spacing={2}>
      {agent.status !== "published" && (
        <Alert severity="info">
          {t(agent.status === "retired" ? "agents.access.retired_hint" : "agents.access.draft_hint")}
        </Alert>
      )}

      <SectionCard title={t("agents.access.users_title")} description={t("agents.access.users_help")}>
        {saveGrants.error && <Alert severity="error">{saveGrants.error}</Alert>}
        <AsyncSection loading={grants.loading} error={grants.error} empty={grants.data.length === 0}>
          <Stack spacing={1}>
            {editable && (
              <PrincipalPicker
                search={search}
                exclude={grantDraft.taken}
                placeholder={t("agents.access.add_users")}
                onPick={grantDraft.add}
              />
            )}
            <PrincipalList
              items={grantDraft.rows}
              emptyText={t("agents.access.nobody")}
              onRemove={editable ? grantDraft.remove : undefined}
            />
          </Stack>
        </AsyncSection>
        {editable && (
          <SaveRow
            dirty={grantDraft.dirty}
            busy={saveGrants.busy}
            onReset={grantDraft.reset}
            onSave={() =>
              saveGrants.run(
                () => agentsApi.setGrants(agent.slug, grantDraft.rows.map((r) => ({ principal_type: r.type, principal_id: r.id }))),
                () => grants.refresh(true),
              )
            }
          />
        )}
      </SectionCard>

      <SectionCard title={t("agents.access.maintainers_title")} description={t("agents.access.maintainers_help")}>
        {saveMembers.error && <Alert severity="error">{saveMembers.error}</Alert>}
        <AsyncSection loading={members.loading} error={members.error} empty={members.data.length === 0}>
          <Stack spacing={1}>
            {editable && (
              <PrincipalPicker
                search={search}
                types={["user"]}
                exclude={memberDraft.taken}
                placeholder={t("agents.access.add_maintainers")}
                onPick={(p) => memberDraft.add({ ...p, role: NEW_MEMBER_ROLE })}
              />
            )}
            <PrincipalList
              items={memberDraft.rows}
              emptyText={t("agents.access.no_maintainers")}
              onRemove={editable ? memberDraft.remove : undefined}
              renderControl={(m) =>
                m.role !== NEW_MEMBER_ROLE && <Chip size="small" variant="outlined" label={m.role} />
              }
            />
          </Stack>
        </AsyncSection>
        {editable && (
          <SaveRow
            dirty={memberDraft.dirty}
            busy={saveMembers.busy}
            onReset={memberDraft.reset}
            onSave={() =>
              saveMembers.run(
                () => agentsApi.setMembers(agent.slug, memberDraft.rows.map((m) => ({ user_id: m.id, role: m.role }))),
                () => members.refresh(true),
              )
            }
          />
        )}
      </SectionCard>

      <AccessCheck agent={agent} />
    </Stack>
  );
}

function SaveRow({ dirty, busy, onReset, onSave }: { dirty: boolean; busy: boolean; onReset: () => void; onSave: () => void }) {
  const { t } = useTranslation();
  return (
    <Stack direction="row" spacing={1} justifyContent="flex-end">
      <Button size="small" disabled={!dirty || busy} onClick={onReset}>
        {t("agents.definition.discard")}
      </Button>
      <Button size="small" variant="contained" disabled={!dirty || busy} onClick={onSave}>
        {busy ? t("common.saving") : t("common.save")}
      </Button>
    </Stack>
  );
}

/** Pick a user, see whether they may use the agent and why. */
function AccessCheck({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const [checked, setChecked] = useState<{ who: PrincipalOption; result: AccessExplanation } | null>(null);
  const check = useAction();

  return (
    <SectionCard title={t("agents.access.check_title")} description={t("agents.access.check_help")}>
      <PrincipalPicker
        search={(q) => agentsApi.findPrincipals(q)}
        types={["user"]}
        placeholder={t("agents.access.check_placeholder")}
        disabled={check.busy}
        onPick={(who) => {
          setChecked(null);
          void check.run(() => agentsApi.explainAccess(agent.slug, who.id), (result) => setChecked({ who, result }));
        }}
      />
      {check.error && <Alert severity="error">{check.error}</Alert>}
      {checked && (
        <Alert severity={checked.result.allowed ? "success" : "warning"}>
          <Box component="strong" sx={{ display: "block" }}>
            {t(checked.result.allowed ? "agents.access.allowed" : "agents.access.refused", { who: checked.who.label })}
          </Box>
          {checked.result.detail}
        </Alert>
      )}
    </SectionCard>
  );
}
