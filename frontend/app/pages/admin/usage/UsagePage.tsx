/**
 * Usage (/admin/usage, `usage:read`): what the instance used of the models,
 * for teams, agents, models and providers -- never for one person.
 *
 * Privacy rules the page shows rather than hides:
 * - Every figure covers at least k people (M04). Groups below k come merged
 *   into one row, labelled as such, and the rule is written above the table;
 *   hours with fewer than k people are gaps in the chart (M10); totals of a
 *   range with fewer than k people are not shown at all.
 * - By team the groups are the tree (`UsageGroups`), as recorded at the time
 *   of use or in today's structure.
 * - With fewer than k active people on the instance, a banner says the totals
 *   are as good as person-level data (S12).
 * - Nothing is ranked: groups are listed by name, the merged row last, and no
 *   column sorts by usage (M11).
 * - Per-person figures only for a usage auditor with person-level reporting on,
 *   after a stated reason (M02, M06, M07): see `PeopleView`.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";

import { isHidden, usageApi, type AdminBucket, type GroupBy, type TeamStructure } from "~/api/usage";
import {
  FULL_ROW,
  PageHeader,
  PanelGrid,
  RangeToggle,
  StatTile,
  WidgetCard,
  WidgetEmpty,
} from "~/components/dashboard/kit";
import { fmtInt } from "~/components/dashboard/format";
import { USAGE_HELP, UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { UsageSectionCard, UsageSeriesChart, UsageTotals } from "~/components/usage/UsageParts";
import { lastDays, usageErrorText } from "~/components/usage/usageFormat";
import { useAsyncData } from "~/lib/useAsyncData";

import PeopleView from "./PeopleView";
import UnhideDialog from "./UnhideDialog";
import UsageGroups from "./UsageGroups";

type RangeKey = "7d" | "30d" | "90d" | "365d";
const RANGE_DAYS: Record<RangeKey, number> = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 };
/** The longest range the server gives by hour. */
const MAX_HOUR_DAYS = 31;
const BUCKETS: AdminBucket[] = ["hour", "day", "month"];
const GROUP_BYS: GroupBy[] = ["team", "agent", "model", "provider"];

export default function UsagePage() {
  const { t, i18n } = useTranslation();
  const [range, setRange] = useState<RangeKey>("30d");
  const [bucket, setBucket] = useState<AdminBucket>("day");
  const [groupBy, setGroupBy] = useState<GroupBy>("team");
  const [structure, setStructure] = useState<TeamStructure>("recorded");
  // Days of the usage time zone, once the server has said which it is.
  const [zone, setZone] = useState<string | undefined>(undefined);
  const dates = lastDays(RANGE_DAYS[range], zone);

  // A usage auditor's reason to see figures about fewer than k people anyway; sent with every load while
  // set (each is recorded), dropped when the server refuses it.
  const [unhideReason, setUnhideReason] = useState<string | null>(null);
  const [unhideOpen, setUnhideOpen] = useState(false);
  // A refusal of that reason stays on screen; the page reloads without the reason meanwhile.
  const [unhideError, setUnhideError] = useState<string | null>(null);

  const usage = useAsyncData(
    () =>
      usageApi
        .admin({
          ...dates,
          bucket,
          group_by: groupBy,
          ...(groupBy === "team" ? { structure } : {}),
          ...(unhideReason ? { reason: unhideReason } : {}),
        })
        .catch((err: unknown) => {
          const words = usageErrorText(err, (key) => t(key));
          if (unhideReason) {
            setUnhideError(words);
            setUnhideReason(null);
          }
          throw new Error(words);
        }),
    [dates.from, dates.to, bucket, groupBy, structure, unhideReason],
  );
  const data = usage.data;
  const loadingFirst = usage.loading && !data;
  const k = data?.min_group_size ?? 5;
  useEffect(() => {
    if (data?.time_zone) setZone(data.time_zone);
  }, [data?.time_zone]);

  // Hours only over a month at most (the server keeps hours for a range of up to 31 days).
  const pickRange = (next: RangeKey) => {
    setRange(next);
    if (bucket === "hour" && RANGE_DAYS[next] > MAX_HOUR_DAYS) setBucket("day");
  };
  const pickBucket = (next: AdminBucket) => {
    setBucket(next);
    if (next === "hour" && RANGE_DAYS[range] > MAX_HOUR_DAYS) setRange("7d");
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pb: 2 }}>
      <PageHeader
        title={t("usage.admin.title")}
        subtitle={t("usage.admin.subtitle")}
        actions={
          <>
            <RangeToggle
              value={range}
              options={(Object.keys(RANGE_DAYS) as RangeKey[]).map((r) => ({ value: r, label: t(`usage.range.${r}`) }))}
              onChange={pickRange}
              ariaLabel={t("usage.range_label")}
            />
            <RangeToggle
              value={bucket}
              options={BUCKETS.map((b) => ({ value: b, label: t(`usage.bucket.${b}`) }))}
              onChange={pickBucket}
              ariaLabel={t("usage.bucket_label")}
            />
            <Tooltip title={t("common.refresh")}>
              <IconButton onClick={() => void usage.refresh()} aria-label={t("common.refresh")}>
                <RefreshIcon />
              </IconButton>
            </Tooltip>
          </>
        }
      />

      {usage.error && (
        <Alert severity="error">
          {t("usage.errors.load_failed")} {usage.error}
        </Alert>
      )}

      {data?.small_instance && (
        <Alert
          severity="warning"
          data-usage-small-instance
          action={
            data.can_view_people && isHidden(data.totals) && !data.unhidden ? (
              <Button color="inherit" size="small" onClick={() => setUnhideOpen(true)}>
                {t("usage.admin.unhide")}
              </Button>
            ) : undefined
          }
        >
          <AlertTitle>{t("usage.admin.small_instance_title")}</AlertTitle>
          {t("usage.admin.small_instance", { k })}
        </Alert>
      )}

      {data?.unhidden && (
        <Alert severity="info" data-usage-unhidden>
          {t("usage.admin.unhidden_note", { k })}
        </Alert>
      )}

      {unhideError && (
        <Alert severity="error" onClose={() => setUnhideError(null)} data-usage-unhide-error>
          {unhideError}
        </Alert>
      )}

      <UnhideDialog
        open={unhideOpen}
        k={k}
        onClose={() => setUnhideOpen(false)}
        onReason={(reason) => {
          setUnhideOpen(false);
          setUnhideError(null);
          setUnhideReason(reason);
        }}
      />

      {data && (
        <Alert severity="info" variant="outlined" data-usage-k-rule>
          {t("usage.admin.k_rule", { k })}
        </Alert>
      )}

      <PanelGrid>
        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t("usage.totals_title")}
            subtitle={data ? t("usage.time_zone", { zone: data.time_zone }) : undefined}
            status={{ loading: loadingFirst, hasData: Boolean(data) }}
          >
            {data && (
              <UsageTotals
                totals={data.totals}
                hiddenHint={t("usage.admin.totals_hidden", { k })}
                before={
                  <StatTile
                    label={t("usage.measures.people")}
                    value={data.totals.people == null ? null : fmtInt(data.totals.people, i18n.language)}
                    emptyText={t("usage.admin.people_below_k", { k })}
                    emptyHint={t("usage.admin.k_rule", { k })}
                  />
                }
              />
            )}
          </WidgetCard>
        </Box>

        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t("usage.series_title")}
            subtitle={
              data?.bucket === "hour" && data.series.some((p) => p.suppressed)
                ? t("usage.admin.hour_suppressed", { k })
                : undefined
            }
            status={{ loading: loadingFirst, hasData: Boolean(data) }}
            empty={data && data.series.length === 0 ? <WidgetEmpty message={t("usage.no_data")} /> : undefined}
          >
            {data && <UsageSeriesChart series={data.series} bucket={data.bucket} />}
          </WidgetCard>
        </Box>

        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t(`usage.admin.group_title.${groupBy}`)}
            subtitle={
              groupBy === "team"
                ? `${t("usage.admin.order_note")} ${t(`usage.admin.structure_hint.${structure}`)}`
                : t("usage.admin.order_note")
            }
            actions={
              <>
                {groupBy === "team" && <UsageHelpLink doc={USAGE_HELP.teamsTree} />}
                {groupBy === "team" && (
                  <RangeToggle
                    value={structure}
                    options={(["recorded", "current"] as TeamStructure[]).map((s) => ({
                      value: s,
                      label: t(`usage.admin.structure.${s}`),
                    }))}
                    onChange={setStructure}
                    ariaLabel={t("usage.admin.structure_label")}
                  />
                )}
                <RangeToggle
                  value={groupBy}
                  options={GROUP_BYS.map((g) => ({ value: g, label: t(`usage.admin.group_by.${g}`) }))}
                  onChange={setGroupBy}
                  ariaLabel={t("usage.admin.group_by_label")}
                />
              </>
            }
            status={{ loading: loadingFirst, hasData: Boolean(data) }}
            empty={data && data.groups.length === 0 ? <WidgetEmpty message={t("usage.no_data")} /> : undefined}
          >
            {/* Keyed by grouping and structure: a new tree starts closed. */}
            {data && <UsageGroups key={`${data.group_by}-${structure}`} data={data} />}
          </WidgetCard>
        </Box>

        {(data?.sections ?? []).map((section, i) => (
          <UsageSectionCard key={`${section.kind}-${i}`} section={section} />
        ))}

        {data && (
          <Box sx={FULL_ROW}>
            {data.can_view_people ? (
              <PeopleView from={data.from} to={data.to} />
            ) : (
              <Typography variant="body2" color="text.secondary" data-usage-people-note>
                {data.person_reporting ? t("usage.admin.reporting_on_no_right") : t("usage.admin.reporting_off")}
              </Typography>
            )}
          </Box>
        )}
      </PanelGrid>
    </Box>
  );
}
