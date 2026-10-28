/**
 * Usage (/admin/usage, `usage:read`): what the instance used of the models,
 * for teams, agents, models and providers -- never for one person.
 *
 * Privacy rules the page shows rather than hides:
 * - Every figure covers at least k people (M04). Groups below k come merged
 *   into one row, labelled as such, and the rule is written above the table;
 *   hours with fewer than k people are gaps in the chart (M10).
 * - With fewer than k active people on the instance, a banner says the totals
 *   are as good as person-level data (S12).
 * - Nothing is ranked: groups are listed by name, the merged row last, and no
 *   column sorts by usage (M11).
 * - Per-person figures only for a usage auditor with person-level reporting on,
 *   after a stated reason (M02, M06, M07): see `PeopleView`.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";

import { usageApi, type AdminBucket, type AdminUsage, type AdminUsageGroup, type GroupBy } from "~/api/usage";
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
import { byLabel, MeasuresTable, UsageSeriesChart, UsageTotals, type MeasuresRow } from "~/components/usage/UsageParts";
import { lastDays } from "~/components/usage/usageFormat";
import { useAsyncData } from "~/lib/useAsyncData";

import PeopleView from "./PeopleView";

type RangeKey = "7d" | "30d" | "90d" | "365d";
const RANGE_DAYS: Record<RangeKey, number> = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 };
const BUCKETS: AdminBucket[] = ["hour", "day", "month"];
const GROUP_BYS: GroupBy[] = ["team", "agent", "model", "provider"];

export default function UsagePage() {
  const { t, i18n } = useTranslation();
  const [range, setRange] = useState<RangeKey>("30d");
  const [bucket, setBucket] = useState<AdminBucket>("day");
  const [groupBy, setGroupBy] = useState<GroupBy>("team");
  const dates = lastDays(RANGE_DAYS[range]);

  const usage = useAsyncData(
    () => usageApi.admin({ ...dates, bucket, group_by: groupBy }),
    [dates.from, dates.to, bucket, groupBy],
  );
  const data = usage.data;
  const loadingFirst = usage.loading && !data;
  const k = data?.min_group_size ?? 5;

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
              onChange={setRange}
              ariaLabel={t("usage.range_label")}
            />
            <RangeToggle
              value={bucket}
              options={BUCKETS.map((b) => ({ value: b, label: t(`usage.bucket.${b}`) }))}
              onChange={setBucket}
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
        <Alert severity="warning" data-usage-small-instance>
          <AlertTitle>{t("usage.admin.small_instance_title")}</AlertTitle>
          {t("usage.admin.small_instance", { k })}
        </Alert>
      )}

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
            subtitle={t("usage.admin.order_note")}
            actions={
              <RangeToggle
                value={groupBy}
                options={GROUP_BYS.map((g) => ({ value: g, label: t(`usage.admin.group_by.${g}`) }))}
                onChange={setGroupBy}
                ariaLabel={t("usage.admin.group_by_label")}
              />
            }
            status={{ loading: loadingFirst, hasData: Boolean(data) }}
            empty={data && data.groups.length === 0 ? <WidgetEmpty message={t("usage.no_data")} /> : undefined}
          >
            {data && <Groups data={data} />}
          </WidgetCard>
        </Box>

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

/**
 * The groups by name, the merged row of groups under k last. A group the
 * server marks suppressed shows no figures.
 */
function Groups({ data }: { data: AdminUsage }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const k = data.min_group_size;
  const named = data.groups.filter((g) => !g.other);
  const merged = data.groups.filter((g) => g.other);
  const nameOf = (g: AdminUsageGroup) => g.label ?? g.key ?? t("usage.admin.no_label");

  const rows: MeasuresRow[] = [
    ...byLabel(named, nameOf, lang).map((g, i) => ({
      key: g.key ?? `none-${i}`,
      label: nameOf(g),
      lead: [g.people == null ? t("usage.admin.people_below_k", { k }) : fmtInt(g.people, lang)],
      measures: g.suppressed ? null : g,
      hidden: t("usage.admin.suppressed", { k }),
    })),
    ...merged.map((g, i) => ({
      key: `other-${i}`,
      label: (
        <Stack component="span">
          <Typography component="span" variant="body2" sx={{ fontWeight: 600 }}>
            {t("usage.admin.other_row", { k })}
          </Typography>
          <Typography component="span" variant="caption" color="text.secondary">
            {t("usage.admin.other_row_hint", { k })}
          </Typography>
        </Stack>
      ),
      lead: [g.people == null ? t("usage.admin.other_people", { k }) : fmtInt(g.people, lang)],
      measures: g,
    })),
  ];

  return (
    <MeasuresTable
      ariaLabel={t(`usage.admin.group_title.${data.group_by}`)}
      labelHeader={t(`usage.admin.group_by.${data.group_by}`)}
      leadHeaders={[t("usage.measures.people")]}
      rows={rows}
    />
  );
}
