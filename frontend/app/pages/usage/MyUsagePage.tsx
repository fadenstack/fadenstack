/**
 * My usage (/usage): everyone's own use of the models (M03), on an account page of its own, so it opens
 * whether or not the chat module is on.
 *
 * Totals, tokens over time, by model, agent and project, the instance's limits and where they stand, and a
 * download of everything kept about the person's usage (M13). The notice at the top says who can see these
 * figures: when person-level reporting is on it names the role, the purpose and how long the figures are
 * kept (M14); when it is off, that nobody sees them alone; either way it links to the organisation's
 * privacy notice when one is set.
 *
 * The periods are days of the usage time zone (where the server's days begin), once the server has named
 * it. Chat projects come without names (they are sealed with the chat's content): the page names them from
 * the person's own projects. Blocks an extension adds (`sections`, e.g. budgets) are drawn generically.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DownloadIcon from "@mui/icons-material/Download";

import { chatApi } from "~/api/chatClient";
import { usageApi, type MyBucket, type MyPrivacy, type UsageBreakdown, type UsageCap } from "~/api/usage";
import { UserPageFrame } from "~/components/UserPageFrame";
import { FULL_ROW, PanelGrid, RangeToggle, WidgetCard, WidgetEmpty } from "~/components/dashboard/kit";
import { fmtInt } from "~/components/dashboard/format";
import { MeasuresTable, UsageSectionCard, UsageSeriesChart, UsageTotals } from "~/components/usage/UsageParts";
import { USAGE_HELP, UsageHelpLink } from "~/components/usage/UsageHelpLink";
import { fmtWhen, lastDays } from "~/components/usage/usageFormat";
import { useAsyncData } from "~/lib/useAsyncData";

type RangeKey = "7d" | "30d" | "90d" | "365d";
const RANGE_DAYS: Record<RangeKey, number> = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 };

export default function MyUsagePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [range, setRange] = useState<RangeKey>("30d");
  const [bucket, setBucket] = useState<MyBucket>("day");
  // Days of the usage time zone, once the server has said which it is.
  const [zone, setZone] = useState<string | undefined>(undefined);
  const dates = lastDays(RANGE_DAYS[range], zone);

  const usage = useAsyncData(() => usageApi.me({ ...dates, bucket }), [dates.from, dates.to, bucket]);
  const data = usage.data;
  const loadingFirst = usage.loading && !data;
  useEffect(() => {
    if (data?.time_zone) setZone(data.time_zone);
  }, [data?.time_zone]);

  // The person's own chat projects, to name the project rows; none when the chat is off or unreachable.
  const projects = useAsyncData(
    () => chatApi.listProjects().then((list) => new Map(list.map((p) => [p.id, p.name]))).catch(() => new Map<string, string>()),
    [],
    { initialValue: new Map<string, string>() },
  );
  const byProject = data?.by_project.map((row) => ({
    ...row,
    label: row.label ?? (row.key ? (projects.data.get(row.key) ?? t("usage.my.unknown_project")) : null),
  }));

  // Back where the person came from (the chat, the console); the chat when this page was opened directly.
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate("/chat"));

  return (
    <UserPageFrame>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={1.5} sx={{ mb: 2 }}>
        <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ minWidth: 0 }}>
          <IconButton onClick={back} aria-label={t("usage.my.back")}>
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
        <Breakdown title={t("usage.my.by_project")} rows={byProject} none={t("usage.my.no_project")} loading={loadingFirst} />

        {(data?.sections ?? []).map((section, i) => (
          <UsageSectionCard key={`${section.kind}-${i}`} section={section} />
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
 * Who sees the person's figures (M14). On: the role, the purpose, the granularity, how long they are kept,
 * and how often they were viewed. Either way the organisation's privacy notice, when it set one.
 */
function PrivacyNotice({ privacy }: { privacy: MyPrivacy }) {
  const { t } = useTranslation();
  const notice = privacy.notice_url ? (
    <Link href={privacy.notice_url} target="_blank" rel="noopener noreferrer" data-usage-notice-link>
      {t("usage.my.notice_link")}
    </Link>
  ) : null;
  if (!privacy.person_reporting) {
    return (
      <Alert severity="info" variant="outlined" sx={{ mb: 2 }} data-usage-privacy="off">
        {t("usage.my.privacy_off")} {notice}
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
      {notice && <Typography variant="body2">{notice}</Typography>}
    </Alert>
  );
}

/**
 * The instance's limits and where each stands; they apply to everyone together (M20). How much of a
 * per-minute limit is used is not known here, and today's tokens show only once enough other people used
 * the instance today: then just the limit.
 */
function Caps({ caps }: { caps: UsageCap[] }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  if (caps.length === 0) return <WidgetEmpty message={t("usage.my.caps_none")} />;
  return (
    <Stack gap={1.75}>
      {caps.map((cap) => {
        const resets = fmtWhen(cap.resets_at, lang);
        const share = cap.used != null && cap.limit > 0 ? Math.min(cap.used / cap.limit, 1) : null;
        return (
          <Box key={cap.kind} data-usage-cap={cap.kind}>
            <Stack direction="row" justifyContent="space-between" gap={1}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t(`usage.caps.${cap.kind}`)}
              </Typography>
              <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                {cap.used != null
                  ? t("usage.my.cap_used", { used: fmtInt(cap.used, lang), limit: fmtInt(cap.limit, lang) })
                  : t("usage.my.cap_limit", { limit: fmtInt(cap.limit, lang) })}
              </Typography>
            </Stack>
            {share != null ? (
              <LinearProgress
                variant="determinate"
                value={share * 100}
                color={share >= 1 ? "error" : share >= 0.8 ? "warning" : "primary"}
                aria-label={t(`usage.caps.${cap.kind}`)}
                sx={{ my: 0.75, height: 8, borderRadius: 999 }}
              />
            ) : (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                {cap.kind === "tokens_per_day" ? t("usage.my.cap_day_hidden") : t("usage.my.cap_minute_note")}
              </Typography>
            )}
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
