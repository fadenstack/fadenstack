/**
 * My usage (/usage): everyone's own use of the models, beside the chat (M03).
 *
 * Totals, tokens over time, by model, agent and project, the instance's limits
 * and where they stand, and an export of everything kept about the person's
 * usage (M13). The notice at the top says who can see these figures: when
 * person-level reporting is on it names the role, the purpose and how long the
 * figures are kept (M14); when it is off, that nobody sees them alone.
 *
 * Blocks an extension adds (`sections`, e.g. budgets) are drawn generically:
 * a heading and their fields.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DownloadIcon from "@mui/icons-material/Download";

import { usageApi, type MyBucket, type MyPrivacy, type UsageBreakdown, type UsageCap, type UsageSection } from "~/api/usage";
import { UserPageFrame } from "~/components/UserPageFrame";
import { FULL_ROW, PanelGrid, RangeToggle, WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { fmtInt } from "~/components/dashboard/format";
import { MeasuresTable, UsageSeriesChart, UsageTotals } from "~/components/usage/UsageParts";
import { USAGE_HELP, UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { fmtWhen, lastDays } from "~/components/usage/usageFormat";
import { useAsyncData } from "~/lib/useAsyncData";

type RangeKey = "7d" | "30d" | "90d" | "365d";
const RANGE_DAYS: Record<RangeKey, number> = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 };

export default function MyUsagePage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [range, setRange] = useState<RangeKey>("30d");
  const [bucket, setBucket] = useState<MyBucket>("day");

  const usage = useAsyncData(() => usageApi.me({ ...lastDays(RANGE_DAYS[range]), bucket }), [range, bucket]);
  const data = usage.data;
  const loadingFirst = usage.loading && !data;

  return (
    <UserPageFrame>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={1.5} sx={{ mb: 2 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ minWidth: 0 }}>
          <IconButton onClick={() => navigate("/chat")} aria-label={t("usage.my.back_to_chat")}>
            <ArrowBackIcon />
          </IconButton>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="h5" component="h1">
              {t("usage.my.title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("usage.my.subtitle")}
            </Typography>
          </Box>
        </Stack>
        <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
          <RangeToggle
            value={range}
            options={(Object.keys(RANGE_DAYS) as RangeKey[]).map((k) => ({ value: k, label: t(`usage.range.${k}`) }))}
            onChange={setRange}
            ariaLabel={t("usage.range_label")}
          />
          <RangeToggle
            value={bucket}
            options={(["day", "month"] as MyBucket[]).map((b) => ({ value: b, label: t(`usage.bucket.${b}`) }))}
            onChange={setBucket}
            ariaLabel={t("usage.bucket_label")}
          />
          <UsageHelpLink doc={USAGE_HELP.ownUsage} />
        </Stack>
      </Stack>

      {usage.error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {t("usage.errors.load_failed")} {usage.error}
        </Alert>
      )}

      {data && <PrivacyNotice privacy={data.privacy} />}

      <PanelGrid>
        <Box sx={FULL_ROW}>
          <WidgetCard
            title={t("usage.totals_title")}
            subtitle={data ? t("usage.time_zone", { zone: data.time_zone }) : undefined}
            status={{ loading: loadingFirst, hasData: Boolean(data) }}
          >
            {data && <UsageTotals totals={data.totals} />}
          </WidgetCard>
        </Box>

        <WidgetCard
          title={t("usage.series_title")}
          status={{ loading: loadingFirst, hasData: Boolean(data) }}
          empty={data && data.series.length === 0 ? <WidgetEmpty message={t("usage.no_data")} /> : undefined}
        >
          {data && <UsageSeriesChart series={data.series} bucket={data.bucket} />}
        </WidgetCard>

        <WidgetCard title={t("usage.my.caps_title")} status={{ loading: loadingFirst, hasData: Boolean(data) }}>
          {data && <Caps caps={data.caps} />}
        </WidgetCard>

        <Breakdown title={t("usage.my.by_model")} rows={data?.by_model} none={t("usage.my.no_model")} loading={loadingFirst} />
        <Breakdown title={t("usage.my.by_agent")} rows={data?.by_agent} none={t("usage.my.no_agent")} loading={loadingFirst} />
        <Breakdown title={t("usage.my.by_project")} rows={data?.by_project} none={t("usage.my.no_project")} loading={loadingFirst} />

        {(data?.sections ?? []).map((section, i) => (
          <SectionCard key={`${section.kind}-${i}`} section={section} lang={i18n.language} />
        ))}

        <Box sx={FULL_ROW}>
          <WidgetCard title={t("usage.my.export_title")} subtitle={t("usage.my.export_hint")}>
            <Stack direction="row" gap={1} flexWrap="wrap">
              <Button
                variant="outlined"
                startIcon={<DownloadIcon />}
                component="a"
                href={usageApi.meExportUrl("json")}
                download
              >
                {t("usage.my.export_json")}
              </Button>
              <Button
                variant="outlined"
                startIcon={<DownloadIcon />}
                component="a"
                href={usageApi.meExportUrl("csv")}
                download
              >
                {t("usage.my.export_csv")}
              </Button>
            </Stack>
          </WidgetCard>
        </Box>
      </PanelGrid>
    </UserPageFrame>
  );
}

/**
 * Who sees the person's figures (M14). On: the role, the purpose, the
 * granularity, how long they are kept, and how often they were viewed.
 */
function PrivacyNotice({ privacy }: { privacy: MyPrivacy }) {
  const { t } = useTranslation();
  if (!privacy.person_reporting) {
    return (
      <Alert severity="info" variant="outlined" sx={{ mb: 2 }} data-usage-privacy="off">
        {t("usage.my.privacy_off")}
      </Alert>
    );
  }
  return (
    <Alert severity="warning" sx={{ mb: 2 }} data-usage-privacy="on">
      <AlertTitle>{t("usage.my.privacy_on_title")}</AlertTitle>
      <Typography variant="body2">
        {t("usage.my.privacy_on", { granularity: t(`usage.granularity.${privacy.granularity}`) })}
      </Typography>
      <Typography variant="body2">{t("usage.my.privacy_on_purpose")}</Typography>
      <Typography variant="body2">{t("usage.my.privacy_on_retention", { days: privacy.retention_days })}</Typography>
      <Typography variant="body2">
        {privacy.views_of_my_data > 0
          ? t("usage.my.privacy_views", { count: privacy.views_of_my_data })
          : t("usage.my.privacy_views_none")}
      </Typography>
    </Alert>
  );
}

/** The instance's limits and where each stands; they apply to everyone together (M20). */
function Caps({ caps }: { caps: UsageCap[] }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  if (caps.length === 0) return <WidgetEmpty message={t("usage.my.caps_none")} />;
  return (
    <Stack gap={1.75}>
      {caps.map((cap) => {
        const share = cap.limit > 0 ? Math.min(cap.used / cap.limit, 1) : 0;
        const resets = fmtWhen(cap.resets_at, lang);
        return (
          <Box key={cap.kind}>
            <Stack direction="row" justifyContent="space-between" gap={1}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t(`usage.caps.${cap.kind}`)}
              </Typography>
              <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {t("usage.my.cap_used", { used: fmtInt(cap.used, lang), limit: fmtInt(cap.limit, lang) })}
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={share * 100}
              color={share >= 1 ? "error" : share >= 0.8 ? "warning" : "primary"}
              aria-label={t(`usage.caps.${cap.kind}`)}
              sx={{ my: 0.75, height: 8, borderRadius: 999 }}
            />
            {resets && (
              <Typography variant="caption" color="text.secondary">
                {t("usage.my.cap_resets", { when: resets })}
              </Typography>
            )}
          </Box>
        );
      })}
      <Typography variant="caption" color="text.secondary">
        {t("usage.my.caps_hint")}
      </Typography>
    </Stack>
  );
}

/** The person's own usage per model, agent or project, as the server lists it. */
function Breakdown({
  title,
  rows,
  none,
  loading,
}: {
  title: string;
  rows: UsageBreakdown[] | undefined;
  none: string;
  loading: boolean;
}) {
  const { t } = useTranslation();
  return (
    <WidgetCard
      title={title}
      status={{ loading, hasData: Boolean(rows) }}
      empty={rows && rows.length === 0 ? <WidgetEmpty message={t("usage.no_data")} /> : undefined}
    >
      {rows && (
        <MeasuresTable
          ariaLabel={title}
          labelHeader={t("usage.col_name")}
          rows={rows.map((r, i) => ({ key: r.key ?? `none-${i}`, label: r.label ?? r.key ?? none, measures: r }))}
        />
      )}
    </WidgetCard>
  );
}

/** A block from an extension: its heading by kind, then its fields as they come. */
function SectionCard({ section, lang }: { section: UsageSection; lang: string }) {
  const { t } = useTranslation();
  const number = new Intl.NumberFormat(lang);
  const show = (value: unknown): string => {
    if (value === null || value === undefined) return "–";
    if (typeof value === "number") return number.format(value);
    if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
    if (typeof value === "string") return value;
    return JSON.stringify(value);
  };
  const fields = Object.entries(section.data ?? {});
  return (
    <WidgetCard title={t(`usage.sections.${section.kind}`, { defaultValue: section.kind })}>
      {fields.length === 0 ? (
        <WidgetEmpty message={t("usage.no_data")} />
      ) : (
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 0.75, m: 0 }}>
          {fields.map(([key, value]) => (
            <Box key={key} sx={{ display: "contents" }}>
              <Typography component="dt" variant="body2" color="text.secondary">
                {t(`usage.section_fields.${key}`, { defaultValue: key })}
              </Typography>
              <Typography component="dd" variant="body2" sx={{ m: 0, textAlign: "right", overflowWrap: "anywhere" }}>
                {show(value)}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </WidgetCard>
  );
}
