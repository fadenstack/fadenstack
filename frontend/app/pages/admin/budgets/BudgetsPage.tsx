/**
 * Budgets (/admin/budgets, administrators): how many tokens or how much money the organisation, a team, an
 * agent or one person may use per day, week or month. Shown while the server offers `budgets`; without it
 * the address leads to the Usage page.
 *
 * Three tabs:
 * - **Budgets**: the list (`BudgetList`) with what each has used, the forecast and the warnings, and the
 *   dialog to create or change one (`BudgetDialog`);
 * - **Chargeback**: a month per team or cost centre, as an internal estimate, with a CSV download;
 * - **Settings**: budgets for single people (root mode) and usage groups from cost centres.
 *
 * The rule every budget follows is on the page, not only in Help: a request needs room in every budget on
 * its way (the organisation's, each team's from its usage group up, its agent's, its person's).
 */
import { useState } from "react";
import { Navigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Tooltip from "@mui/material/Tooltip";
import AddIcon from "@mui/icons-material/Add";
import RefreshIcon from "@mui/icons-material/Refresh";

import { hasFeature } from "~/api/about";
import { adminGroups, adminUsers, type AdminUser, type Group } from "~/api/admin";
import { agentsApi, type Agent } from "~/api/agents";
import { BUDGETS, budgetsApi, type Budget, type BudgetSettings } from "~/api/budgets";
import { observability } from "~/api/observability";
import { usageApi } from "~/api/usage";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { BUDGETS_HELP } from "~/components/budgets/budgetsHelp";
import { budgetErrorText } from "~/components/budgets/budgetFormat";
import { PageHeader, WidgetEmpty } from "~/components/dashboard/kit";
import { UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { useAsyncData } from "~/lib/useAsyncData";

import BudgetDialog from "./BudgetDialog";
import BudgetList from "./BudgetList";
import BudgetSettingsTab from "./BudgetSettingsTab";
import ChargebackTab from "./ChargebackTab";

type PageTab = "budgets" | "chargeback" | "settings";
const TABS: PageTab[] = ["budgets", "chargeback", "settings"];

interface Lists {
  teams: Group[];
  agents: Agent[];
  users: AdminUser[];
  /** The currencies active prices are in. */
  currencies: string[];
  minGroupSize: number;
  personReporting: boolean;
}

const EMPTY_LISTS: Lists = { teams: [], agents: [], users: [], currencies: [], minGroupSize: 5, personReporting: false };

/** What the dialog picks from and the list names; each part may fail on its own and is then empty. */
async function loadLists(): Promise<Lists> {
  const [teams, agents, users, prices, usage] = await Promise.all([
    adminGroups.list().catch(() => [] as Group[]),
    agentsApi.list().catch(() => [] as Agent[]),
    adminUsers.list().catch(() => [] as AdminUser[]),
    observability.listPricing().catch(() => []),
    usageApi.settings().catch(() => null),
  ]);
  const currencies = [...new Set(prices.filter((p) => p.active !== false).map((p) => p.currency).filter(Boolean))];
  return {
    teams,
    agents,
    users,
    currencies,
    minGroupSize: usage?.min_group_size ?? 5,
    personReporting: usage?.person_reporting ?? false,
  };
}

export default function BudgetsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const offered = useAsyncData<boolean | null>(() => hasFeature(BUDGETS), [], { initialValue: null });

  if (offered.data === false) return <Navigate to="/admin/usage" replace />;
  if (offered.data === null) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={28} aria-label={t("common.loading")} />
      </Box>
    );
  }

  const asked = searchParams.get("tab") as PageTab | null;
  const tab: PageTab = asked && TABS.includes(asked) ? asked : "budgets";
  const select = (next: PageTab) => {
    const params = new URLSearchParams(searchParams);
    if (next === "budgets") params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  return <BudgetsBody tab={tab} onTab={select} />;
}

function BudgetsBody({ tab, onTab }: { tab: PageTab; onTab: (tab: PageTab) => void }) {
  const { t } = useTranslation();
  const budgets = useAsyncData(
    () =>
      budgetsApi.list().catch((err: unknown) => {
        throw new Error(budgetErrorText(err, (key, options) => t(key, options)));
      }),
    [],
  );
  const lists = useAsyncData(loadLists, [], { initialValue: EMPTY_LISTS });
  const settings = useAsyncData(() => budgetsApi.settings().catch(() => null as BudgetSettings | null), []);
  const [editing, setEditing] = useState<Budget | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState<Budget | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [notice, setNotice] = useState<{ severity: "success" | "error"; text: string } | null>(null);

  const personBudgets = settings.data?.person_budgets ?? false;
  const list = budgets.data ?? [];

  function openDialog(budget: Budget | null) {
    setEditing(budget);
    setDialogOpen(true);
    setNotice(null);
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await budgetsApi.remove(deleting.id);
      setNotice({ severity: "success", text: t("budgets.deleted", { name: deleting.name }) });
      setDeleting(null);
      await budgets.refresh(true);
    } catch (err) {
      setNotice({ severity: "error", text: budgetErrorText(err, (key, options) => t(key, options)) });
      setDeleting(null);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pb: 2 }} data-budgets-page>
      <PageHeader
        title={t("budgets.title")}
        subtitle={t("budgets.subtitle")}
        actions={
          <>
            <UsageHelpLink doc={BUDGETS_HELP.page} />
            {tab === "budgets" && (
              <>
                <Tooltip title={t("common.refresh")}>
                  <IconButton onClick={() => void budgets.refresh()} aria-label={t("common.refresh")}>
                    <RefreshIcon />
                  </IconButton>
                </Tooltip>
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => openDialog(null)}>
                  {t("budgets.new")}
                </Button>
              </>
            )}
          </>
        }
      />

      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Tabs
          value={tab}
          onChange={(_, next: PageTab) => onTab(next)}
          variant="scrollable"
          allowScrollButtonsMobile
          aria-label={t("budgets.title")}
        >
          {TABS.map((name) => (
            <Tab key={name} value={name} label={t(`budgets.tabs.${name}`)} />
          ))}
        </Tabs>
      </Box>

      {tab === "budgets" && (
        <>
          <Alert severity="info" variant="outlined" data-budget-rule>
            {t("budgets.rule")} <UsageHelpLink doc={BUDGETS_HELP.tree} />
          </Alert>
          {notice && (
            <Alert severity={notice.severity} onClose={() => setNotice(null)}>
              {notice.text}
            </Alert>
          )}
          {budgets.error && (
            <Alert
              severity="error"
              action={
                <Button color="inherit" size="small" onClick={() => void budgets.refresh()}>
                  {t("common.refresh")}
                </Button>
              }
            >
              {t("budgets.load_failed")} {budgets.error}
            </Alert>
          )}
          {budgets.loading && !budgets.data ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress size={28} aria-label={t("common.loading")} />
            </Box>
          ) : budgets.data && list.length === 0 ? (
            <WidgetEmpty
              message={t("budgets.none")}
              action={
                <Button variant="outlined" startIcon={<AddIcon />} onClick={() => openDialog(null)}>
                  {t("budgets.new")}
                </Button>
              }
            />
          ) : (
            <BudgetList
              budgets={list}
              users={lists.data.users}
              currencies={lists.data.currencies}
              onEdit={(budget) => openDialog(budget)}
              onDelete={(budget) => setDeleting(budget)}
            />
          )}
        </>
      )}

      {tab === "chargeback" && <ChargebackTab />}

      {tab === "settings" && (
        <BudgetSettingsTab teams={lists.data.teams} onSaved={() => void settings.refresh(true)} />
      )}

      <BudgetDialog
        open={dialogOpen}
        budget={editing}
        personBudgets={personBudgets}
        minGroupSize={lists.data.minGroupSize}
        personReporting={lists.data.personReporting}
        teams={lists.data.teams}
        agents={lists.data.agents}
        users={lists.data.users}
        currencies={lists.data.currencies}
        onClose={() => setDialogOpen(false)}
        onSaved={(saved) => {
          setDialogOpen(false);
          setNotice({
            severity: "success",
            text: editing ? t("budgets.saved", { name: saved.name }) : t("budgets.created", { name: saved.name }),
          });
          void budgets.refresh(true);
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        title={t("budgets.delete_title")}
        message={deleting ? t("budgets.delete_body", { name: deleting.name }) : ""}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        confirmColor="error"
        loading={deleteBusy}
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
      />
    </Box>
  );
}
