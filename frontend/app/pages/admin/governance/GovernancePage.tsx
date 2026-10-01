/**
 * Governance (administrators): the rules every request is held to -- tool
 * rules, tool classes -- and the log of what the checks decided. The gateway
 * applies a change within 30 seconds.
 */
import { useTranslation } from "react-i18next";

import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";

import { PageHeader } from "~/components/dashboard/kit";
import { DecisionsTable } from "~/components/governance/DecisionsTable";
import { ToolClassesTable } from "~/components/governance/ToolClassesTable";
import { ToolRulesTable } from "~/components/governance/ToolRulesTable";
import { useTabParam } from "~/lib/useTabParam";

const TABS = {
  rules: ToolRulesTable,
  classes: ToolClassesTable,
  decisions: DecisionsTable,
} as const;
type GovernanceTab = keyof typeof TABS;
const TAB_NAMES = Object.keys(TABS) as GovernanceTab[];

export default function GovernancePage() {
  const { t } = useTranslation();
  const [tab, setTab] = useTabParam(TAB_NAMES);
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
        {TAB_NAMES.map((name) => (
          <Tab key={name} value={name} label={t(`governance.tab.${name}`)} />
        ))}
      </Tabs>
      <Panel />
    </Stack>
  );
}
