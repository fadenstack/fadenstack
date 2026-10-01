/**
 * What an agent did: its usage over a window, what the SDKs reported (tools,
 * approvals, local MCP servers, sessions) and the devices signed in to it.
 * Names, counts and durations -- never content.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import LogoutIcon from "@mui/icons-material/Logout";

import { agentsApi, revokeSignIn, type Agent, type AgentSignIn, type AgentUsage, type ClientEvent } from "~/api/agents";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { fmtAgo, fmtCompact, fmtInt, fmtMs } from "~/components/dashboard/format";
import { RangeToggle, StatGrid, StatTile, TimeSeriesChart, WidgetCard } from "~/components/dashboard/kit";
import { formatDateTime, localDay } from "~/lib/dates";
import { useAction } from "~/lib/useAction";
import { useAsyncData } from "~/lib/useAsyncData";
import { usePrincipalNames } from "~/lib/usePrincipalNames";

import { EVENT_TYPES, summarizeEvent } from "./agentEvents";

const RANGES = ["7", "30", "90"] as const;
type Range = (typeof RANGES)[number];

export interface AgentActivityPanelProps {
  agent: Agent;
}

export function AgentActivityPanel({ agent }: AgentActivityPanelProps) {
  const [range, setRange] = useState<Range>("30");
  const [eventType, setEventType] = useState("");

  const usage = useAsyncData(() => agentsApi.usage(agent.slug, Number(range)), [agent.slug, range], {
    initialValue: null as AgentUsage | null,
  });
  const events = useAsyncData(() => agentsApi.events(agent.slug, { type: eventType || undefined }), [agent.slug, eventType], {
    initialValue: [] as ClientEvent[],
  });
  const signIns = useAsyncData(() => agentsApi.signIns(agent.slug), [agent.slug], { initialValue: [] as AgentSignIn[] });

  const nameOf = usePrincipalNames(
    [...events.data.map((e) => e.user_id), ...signIns.data.map((s) => s.user_id)],
    agentsApi.namePrincipals,
  );

  return (
    <Stack spacing={2}>
      <UsageCard usage={usage} range={range} onRange={setRange} />
      <EventsTable events={events} type={eventType} onType={setEventType} nameOf={nameOf} />
      <SignInsTable agent={agent} signIns={signIns} nameOf={nameOf} />
    </Stack>
  );
}

type Loaded<T> = ReturnType<typeof useAsyncData<T>>;

function UsageCard({ usage, range, onRange }: { usage: Loaded<AgentUsage | null>; range: Range; onRange: (r: Range) => void }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const u = usage.data;
  const days = u?.by_day ?? [];
  const dayFormat = new Intl.DateTimeFormat(i18n.language, { day: "numeric", month: "short" });
  const value = (n: number | undefined, format = fmtInt) => (u ? format(n, i18n.language) : null);

  return (
    <WidgetCard
      title={t("agents.activity.usage_title")}
      subtitle={t("agents.activity.usage_subtitle", { days: range })}
      actions={
        <RangeToggle
          value={range}
          options={RANGES.map((r) => ({ value: r, label: t("agents.activity.days", { count: Number(r) }) }))}
          onChange={onRange}
          ariaLabel={t("agents.activity.range")}
        />
      }
      status={{ loading: usage.loading && !u, error: usage.error, hasData: Boolean(u), onRetry: () => void usage.refresh() }}
    >
      <Stack spacing={2}>
        <StatGrid>
          <StatTile label={t("agents.activity.requests")} value={value(u?.requests)} />
          <StatTile
            label={t("agents.activity.errors")}
            value={value(u?.errors)}
            tone={u && u.errors > 0 ? "warning" : "default"}
          />
          <StatTile
            label={t("agents.activity.tokens")}
            value={value(u?.total_tokens, fmtCompact)}
            detail={
              u &&
              t("agents.activity.tokens_detail", {
                prompt: fmtCompact(u.prompt_tokens, i18n.language),
                completion: fmtCompact(u.completion_tokens, i18n.language),
              })
            }
          />
          <StatTile label={t("agents.activity.users")} value={value(u?.users)} />
          <StatTile label={t("agents.activity.conversations")} value={value(u?.conversations)} />
        </StatGrid>
        {days.length > 0 ? (
          <TimeSeriesChart
            x={days.map((d) => d.day)}
            xFormat={(label) => dayFormat.format(localDay(String(label)))}
            series={[
              {
                id: "requests",
                label: t("agents.activity.requests"),
                data: days.map((d) => d.requests),
                color: theme.palette.primary.main,
              },
            ]}
            ariaLabel={t("agents.activity.chart_aria", { days: range })}
          />
        ) : (
          u && (
            <Typography variant="body2" color="text.secondary">
              {t("agents.activity.no_usage")}
            </Typography>
          )
        )}
      </Stack>
    </WidgetCard>
  );
}

function EventsTable({
  events,
  type,
  onType,
  nameOf,
}: {
  events: Loaded<ClientEvent[]>;
  type: string;
  onType: (type: string) => void;
  nameOf: (id: string) => string;
}) {
  const { t, i18n } = useTranslation();

  const columns: ColumnDef<ClientEvent>[] = [
    {
      key: "when",
      label: t("agents.activity.when"),
      sortable: true,
      sortValue: (e) => e.occurred_at ?? e.created_at,
      render: (e) => formatDateTime(e.occurred_at ?? e.created_at),
      minWidth: 170,
    },
    {
      key: "type",
      label: t("agents.activity.type"),
      render: (e) => <Chip size="small" variant="outlined" label={t(`agents.activity.event.${e.type}`, { defaultValue: e.type })} />,
      searchValue: (e) => e.type,
      minWidth: 150,
    },
    {
      key: "what",
      label: t("agents.activity.what"),
      searchValue: (e) => summarizeEvent(e).subject,
      render: (e) => {
        const s = summarizeEvent(e);
        const parts = [
          ...s.facts,
          s.durationMs !== undefined ? fmtMs(s.durationMs, i18n.language) : null,
          s.count ? t(`agents.activity.count_${s.count.key}`, { count: s.count.value }) : null,
        ].filter(Boolean);
        return (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
            <Typography variant="body2" fontFamily="monospace" noWrap>
              {s.subject}
            </Typography>
            {s.result && (
              <Chip
                size="small"
                color={s.tone}
                label={t(`agents.activity.result.${s.result}`, { defaultValue: s.result })}
              />
            )}
            {parts.length > 0 && (
              <Typography variant="caption" color="text.secondary" noWrap>
                {parts.join(" · ")}
              </Typography>
            )}
          </Stack>
        );
      },
      minWidth: 280,
    },
    {
      key: "user",
      label: t("agents.activity.user"),
      searchValue: (e) => nameOf(e.user_id),
      render: (e) => nameOf(e.user_id),
      minWidth: 180,
    },
  ];

  return (
    <DataTable
      title={t("agents.activity.events_title")}
      rows={events.data}
      columns={columns}
      rowKey={(e) => e.id}
      loading={events.loading}
      error={events.error}
      onRefresh={() => void events.refresh()}
      emptyMessage={t("agents.activity.no_events")}
      columnFilters={[
        {
          label: t("agents.activity.type"),
          value: type,
          onChange: onType,
          options: [
            { value: "", label: t("common.all") },
            ...EVENT_TYPES.map((v) => ({ value: v, label: t(`agents.activity.event.${v}`) })),
          ],
          minWidth: 180,
        },
      ]}
      pagination={25}
    />
  );
}

function SignInsTable({
  agent,
  signIns,
  nameOf,
}: {
  agent: Agent;
  signIns: Loaded<AgentSignIn[]>;
  nameOf: (id: string) => string;
}) {
  const { t, i18n } = useTranslation();
  const [revoking, setRevoking] = useState<AgentSignIn | null>(null);
  const revoke = useAction();

  /** A sign-in ends at its hard limit or when it has been idle too long, whichever is first. */
  const endsAt = (s: AgentSignIn) => (s.idle_expires_at < s.expires_at ? s.idle_expires_at : s.expires_at);

  const columns: ColumnDef<AgentSignIn>[] = [
    {
      key: "device",
      label: t("agents.activity.device"),
      searchValue: (s) => s.device_label ?? s.device_id,
      render: (s) => s.device_label ?? <Typography variant="body2" fontFamily="monospace">{s.device_id}</Typography>,
      minWidth: 180,
    },
    { key: "user", label: t("agents.activity.user"), searchValue: (s) => nameOf(s.user_id), render: (s) => nameOf(s.user_id), minWidth: 180 },
    {
      key: "signed_in",
      label: t("agents.activity.signed_in"),
      sortable: true,
      sortValue: (s) => s.created_at,
      render: (s) => formatDateTime(s.created_at),
      minWidth: 170,
    },
    {
      key: "last_used",
      label: t("agents.activity.last_used"),
      sortable: true,
      sortValue: (s) => s.last_used_at ?? "",
      render: (s) => (s.last_used_at ? fmtAgo(s.last_used_at, i18n.language) : t("agents.activity.never_used")),
      minWidth: 130,
    },
    {
      key: "ends",
      label: t("agents.activity.ends"),
      sortable: true,
      sortValue: endsAt,
      render: (s) => formatDateTime(endsAt(s)),
      minWidth: 170,
    },
  ];
  if (agent.can_manage) {
    columns.push({
      key: "actions",
      label: t("common.actions"),
      align: "right",
      hideable: false,
      render: (s) => (
        <Button size="small" color="error" startIcon={<LogoutIcon />} onClick={() => setRevoking(s)}>
          {t("agents.activity.sign_out")}
        </Button>
      ),
    });
  }

  return (
    <>
      {revoke.error && (
        <Alert severity="error" onClose={() => revoke.setError(null)}>
          {revoke.error}
        </Alert>
      )}
      <DataTable
        title={t("agents.activity.sign_ins_title")}
        rows={signIns.data}
        columns={columns}
        rowKey={(s) => s.device_id}
        loading={signIns.loading}
        error={signIns.error}
        onRefresh={() => void signIns.refresh()}
        emptyMessage={t("agents.activity.no_sign_ins")}
      />
      <ConfirmDialog
        open={revoking !== null}
        title={t("agents.activity.sign_out_title")}
        message={t("agents.activity.sign_out_confirm", {
          device: revoking?.device_label ?? revoking?.device_id,
          user: revoking ? nameOf(revoking.user_id) : "",
        })}
        confirmLabel={t("agents.activity.sign_out")}
        cancelLabel={t("common.cancel")}
        loading={revoke.busy}
        onClose={() => setRevoking(null)}
        onConfirm={() =>
          void revoke.run(
            () => revokeSignIn(revoking!.device_id),
            () => {
              setRevoking(null);
              return signIns.refresh(true);
            },
          )
        }
      />
    </>
  );
}
