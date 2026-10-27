/**
 * Governance (administrators): the rules every request is held to -- tool
 * rules, tool classes, and where the server offers them tool mode limits --
 * and the log of what the checks decided. The gateway applies a change within
 * 30 seconds.
 */
import { useTranslation } from "react-i18next";

import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";

import { hasFeature } from "~/api/about";
import { TOOL_MODE_CEILINGS } from "~/api/toolCeilings";
import { PageHeader } from "~/components/dashboard/kit";
import { DecisionsTable } from "~/components/governance/DecisionsTable";
import { ToolCeilingsTable } from "~/components/governance/ToolCeilingsTable";
import { ToolClassesTable } from "~/components/governance/ToolClassesTable";
import { ToolRulesTable } from "~/components/governance/ToolRulesTable";
import { useAsyncData } from "~/lib/useAsyncData";
import { useTabParam } from "~/lib/useTabParam";

const TABS = {
  rules: ToolRulesTable,
  classes: ToolClassesTable,
  limits: ToolCeilingsTable,
  decisions: DecisionsTable,
} as const;
type GovernanceTab = keyof typeof TABS;
const ALL_TABS = Object.keys(TABS) as GovernanceTab[];

export default function GovernancePage() {
  const { t } = useTranslation();
  // Tool mode limits only where the server offers them (`GET /api/about`).
  const { data: limits } = useAsyncData(() => hasFeature(TOOL_MODE_CEILINGS), [], { initialValue: false });
  const offered = ALL_TABS.filter((name) => name !== "limits" || limits);
  const [tab, setTab] = useTabParam(offered);
  const Panel = TABS[tab];

  return (
    <Stack spacing={2}>
      <PageHeader title={t("governance.title")} subtitle={t("governance.subtitle")} />
      <Tabs
        value={tab}
        onChange={(_, next: GovernanceTab) => setTab(next)}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ borderBottom: 1, borderColor: "divider" }}
      >
        {offered.map((name) => (
          <Tab key={name} value={name} label={t(`governance.tab.${name}`)} />
        ))}
      </Tabs>
      <Panel />
    </Stack>
  );
}
