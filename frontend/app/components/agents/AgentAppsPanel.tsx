/**
 * What the apps that run an agent bring: their tools with the classes their
 * developers gave them, their greeting, and which versions are in use -- set
 * against the agent's latest version. New tools are held back by the gateway
 * until a version with them is published; one click takes everything in as
 * the apps suggest, and every class can be changed first. The app's word is a
 * suggestion: the admin's choice goes into the contract.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip, { type ChipProps } from "@mui/material/Chip";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import {
  agentsApi,
  type Agent,
  type AppSeen,
  type AppsReview,
  type ToolClass,
  type ToolReview,
  type ToolReviewStatus,
} from "~/api/agents";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { fmtAgo, fmtInt } from "~/components/dashboard/format";
import { useAction } from "~/lib/useAction";

const CLASSES: ToolClass[] = ["read", "write", "destructive", "unknown"];
const PENDING: ToolReviewStatus[] = ["new", "changed", "riskier"];
/** A tool left out of this version. */
const SKIP = "skip";
type Choice = ToolClass | typeof SKIP;

const STATUS_COLOR: Record<ToolReviewStatus, ChipProps["color"]> = {
  new: "primary",
  changed: "warning",
  riskier: "warning",
  unused: "default",
  same: "success",
};

/** What the review suggests for a tool: the app's class, or the contract's for a changed one. */
export function suggestedChoice(tool: ToolReview): Choice {
  if (tool.status === "changed") return tool.contract_class ?? tool.suggested_class;
  return tool.suggested_class;
}

export interface AgentAppsPanelProps {
  agent: Agent;
  review: AppsReview | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  /** A version was made (and maybe published): reload the agent, its versions and this review. */
  onChanged: () => void;
}

export function AgentAppsPanel({ agent, review, loading, error, onRefresh, onChanged }: AgentAppsPanelProps) {
  const { t } = useTranslation();
  const editable = agent.can_manage;
  const accept = useAction();
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [removing, setRemoving] = useState<Set<string>>(new Set());
  const [takeUi, setTakeUi] = useState(true);

  // Each review starts from what it suggests.
  useEffect(() => {
    setChoices(Object.fromEntries((review?.tools ?? []).filter((x) => PENDING.includes(x.status)).map((x) => [x.name, suggestedChoice(x)])));
    setRemoving(new Set());
    setTakeUi(true);
  }, [review]);

  const taken = useMemo(
    () => Object.entries(choices).filter(([, c]) => c !== SKIP).map(([name, risk]) => ({ name, risk: risk as ToolClass })),
    [choices],
  );
  const anything = taken.length > 0 || removing.size > 0 || Boolean(review?.ui && takeUi);

  const run = (publish: boolean) =>
    void accept.run(
      () => agentsApi.acceptApps(agent.slug, { tools: taken, remove: [...removing], ui: Boolean(review?.ui && takeUi), publish }),
      onChanged,
    );
  const publishLatest = () => void accept.run(() => agentsApi.publish(agent.slug, review!.compared_with!), onChanged);

  if (!review) {
    return error ? (
      <Alert severity="error" action={<Button onClick={onRefresh}>{t("dashboard.retry")}</Button>}>
        {error}
      </Alert>
    ) : (
      <Typography color="text.secondary">{loading ? t("common.loading") : null}</Typography>
    );
  }

  return (
    <Stack spacing={2}>
      <Summary review={review} editable={editable} anything={anything} busy={accept.busy} onAccept={run} onPublish={publishLatest} />
      {accept.error && (
        <Alert severity="error" onClose={() => accept.setError(null)}>
          {accept.error}
        </Alert>
      )}
      <ToolsTable
        review={review}
        editable={editable}
        choices={choices}
        onChoose={(name, choice) => setChoices((prev) => ({ ...prev, [name]: choice }))}
        removing={removing}
        onRemove={(name, on) =>
          setRemoving((prev) => {
            const next = new Set(prev);
            if (on) next.add(name);
            else next.delete(name);
            return next;
          })
        }
        loading={loading}
        onRefresh={onRefresh}
      />
      {review.ui && <SuggestedGreeting ui={review.ui} editable={editable} take={takeUi} onTake={setTakeUi} />}
      {review.apps.length > 0 && <AppsTable apps={review.apps} loading={loading} />}
    </Stack>
  );
}

/** Where the agent stands: nothing reported, changes to take, accepted but not live, or all in. */
function Summary({
  review,
  editable,
  anything,
  busy,
  onAccept,
  onPublish,
}: {
  review: AppsReview;
  editable: boolean;
  anything: boolean;
  busy: boolean;
  onAccept: (publish: boolean) => void;
  onPublish: () => void;
}) {
  const { t } = useTranslation();
  if (review.apps.length === 0) {
    return <Alert severity="info">{t("agents.apps.none")}</Alert>;
  }
  if (review.pending > 0 || (review.ui && editable)) {
    return (
      <Stack spacing={1}>
        {review.unrestricted && <Alert severity="warning">{t("agents.apps.unrestricted")}</Alert>}
        <Alert
          severity={review.pending > 0 ? "warning" : "info"}
          action={
            editable && (
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
                <Button size="small" variant="contained" disabled={busy || !anything} onClick={() => onAccept(true)}>
                  {t("agents.apps.accept_publish")}
                </Button>
                <Button size="small" color="inherit" disabled={busy || !anything} onClick={() => onAccept(false)}>
                  {t("agents.apps.accept_draft")}
                </Button>
              </Stack>
            )
          }
        >
          {review.pending > 0
            ? t("agents.apps.pending", { count: review.pending, version: review.compared_with })
            : t("agents.apps.greeting_only")}
          {editable && review.pending > 0 && (
            <Typography variant="body2" color="text.secondary">
              {t("agents.apps.pending_hint")}
            </Typography>
          )}
        </Alert>
      </Stack>
    );
  }
  if (review.live_pending > 0) {
    return (
      <Alert
        severity="warning"
        action={
          editable && (
            <Button size="small" variant="contained" disabled={busy} onClick={onPublish}>
              {t("agents.publish_version", { version: review.compared_with })}
            </Button>
          )
        }
      >
        {t("agents.apps.not_live", { count: review.live_pending, version: review.compared_with })}
      </Alert>
    );
  }
  return <Alert severity="success">{t("agents.apps.all_in", { version: review.compared_with })}</Alert>;
}

function ToolsTable({
  review,
  editable,
  choices,
  onChoose,
  removing,
  onRemove,
  loading,
  onRefresh,
}: {
  review: AppsReview;
  editable: boolean;
  choices: Record<string, Choice>;
  onChoose: (name: string, choice: Choice) => void;
  removing: Set<string>;
  onRemove: (name: string, on: boolean) => void;
  loading: boolean;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const className = (c: ToolClass | null) => t(`agents.apps.class.${c ?? "unknown"}`);
  const appLabel = (label: string) => (label === "requests" ? t("agents.apps.requests") : label);

  const columns: ColumnDef<ToolReview>[] = [
    {
      key: "tool",
      label: t("agents.apps.tool"),
      searchValue: (x) => `${x.name} ${x.description ?? ""}`,
      minWidth: 260,
      render: (x) => (
        <Stack spacing={0.5} sx={{ minWidth: 0, py: 0.5 }}>
          <Typography variant="body2" fontFamily="monospace">
            {x.name}
          </Typography>
          {x.description && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
            >
              {x.description}
            </Typography>
          )}
          {x.warnings.length > 0 && (
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {x.warnings.map((w) => (
                <Tooltip key={w} title={t(`agents.apps.warning.${w}.detail`)}>
                  <Chip size="small" color="error" variant="outlined" label={t(`agents.apps.warning.${w}.label`)} />
                </Tooltip>
              ))}
            </Stack>
          )}
        </Stack>
      ),
    },
    {
      key: "status",
      label: t("agents.apps.status_column"),
      minWidth: 120,
      searchValue: (x) => x.status,
      render: (x) => (
        <Tooltip title={t(`agents.apps.status.${x.status}.detail`)}>
          <Chip size="small" color={STATUS_COLOR[x.status]} variant={x.status === "same" ? "outlined" : "filled"} label={t(`agents.apps.status.${x.status}.label`)} />
        </Tooltip>
      ),
    },
    {
      key: "class",
      label: t("agents.apps.class_column"),
      minWidth: 220,
      render: (x) => {
        if (editable && PENDING.includes(x.status)) {
          return (
            <Stack spacing={0.25}>
              <TextField
                select
                size="small"
                value={choices[x.name] ?? suggestedChoice(x)}
                onChange={(e) => onChoose(x.name, e.target.value as Choice)}
                slotProps={{ htmlInput: { "aria-label": t("agents.apps.class_for", { tool: x.name }) } }}
                sx={{ minWidth: 190 }}
              >
                {CLASSES.map((c) => (
                  <MenuItem key={c} value={c}>
                    {className(c)}
                  </MenuItem>
                ))}
                <MenuItem value={SKIP}>{t("agents.apps.skip")}</MenuItem>
              </TextField>
              <Typography variant="caption" color="text.secondary">
                {x.in_contract
                  ? t("agents.apps.class_now", { app: className(x.suggested_class), contract: className(x.contract_class) })
                  : t("agents.apps.class_app", { app: className(x.suggested_class) })}
              </Typography>
            </Stack>
          );
        }
        if (editable && x.status === "unused") {
          return (
            <FormControlLabel
              control={<Checkbox size="small" checked={removing.has(x.name)} onChange={(e) => onRemove(x.name, e.target.checked)} />}
              label={t("agents.apps.remove")}
            />
          );
        }
        return <Typography variant="body2">{className(x.in_contract ? x.contract_class : x.suggested_class)}</Typography>;
      },
    },
    {
      key: "apps",
      label: t("agents.apps.brought_by"),
      minWidth: 180,
      searchValue: (x) => x.apps.join(" "),
      render: (x) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {x.apps.map((a) => (
            <Chip key={a} size="small" variant="outlined" label={appLabel(a)} />
          ))}
        </Stack>
      ),
    },
  ];

  return (
    <DataTable
      title={t("agents.apps.tools_title")}
      rows={review.tools}
      columns={columns}
      rowKey={(x) => x.name}
      loading={loading}
      onRefresh={onRefresh}
      emptyMessage={t("agents.apps.no_tools")}
    />
  );
}

function SuggestedGreeting({
  ui,
  editable,
  take,
  onTake,
}: {
  ui: NonNullable<AppsReview["ui"]>;
  editable: boolean;
  take: boolean;
  onTake: (take: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={1}>
        <Typography variant="subtitle2">{t("agents.apps.greeting_title")}</Typography>
        {ui.greeting && <Typography variant="body2">{ui.greeting}</Typography>}
        {ui.suggestions && ui.suggestions.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
            {ui.suggestions.map((s) => (
              <Chip key={s} size="small" label={s} />
            ))}
          </Stack>
        )}
        {editable && (
          <Box>
            <FormControlLabel
              control={<Checkbox size="small" checked={take} onChange={(e) => onTake(e.target.checked)} />}
              label={t("agents.apps.greeting_take")}
            />
          </Box>
        )}
      </Stack>
    </Paper>
  );
}

function AppsTable({ apps, loading }: { apps: AppSeen[]; loading: boolean }) {
  const { t, i18n } = useTranslation();
  const columns: ColumnDef<AppSeen>[] = [
    {
      key: "app",
      label: t("agents.apps.app"),
      minWidth: 200,
      searchValue: (a) => `${a.name} ${a.version}`,
      render: (a) =>
        a.source === "request" ? (
          <Typography variant="body2" color="text.secondary">
            {t("agents.apps.requests")}
          </Typography>
        ) : (
          <Stack spacing={0}>
            <Typography variant="body2">
              {a.name} <Box component="span" sx={{ color: "text.secondary" }}>{a.version}</Box>
            </Typography>
            {a.sdk && (
              <Typography variant="caption" color="text.secondary">
                {a.sdk}
              </Typography>
            )}
          </Stack>
        ),
    },
    {
      key: "seen",
      label: t("agents.apps.last_seen"),
      sortable: true,
      sortValue: (a) => a.last_seen_at,
      minWidth: 130,
      render: (a) => fmtAgo(a.last_seen_at, i18n.language),
    },
    {
      key: "reports",
      label: t("agents.apps.reports"),
      align: "right",
      minWidth: 90,
      render: (a) => fmtInt(a.reports, i18n.language),
    },
    {
      key: "who",
      label: t("agents.apps.reported_by"),
      minWidth: 180,
      searchValue: (a) => a.last_user_label ?? a.last_user_id,
      render: (a) => a.last_user_label ?? a.last_user_id,
    },
    {
      key: "context",
      label: t("agents.apps.context"),
      minWidth: 200,
      render: (a) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {a.context.map((c) => (
            <Tooltip key={c.id} title={c.title ?? ""}>
              <Chip size="small" variant="outlined" label={c.id} sx={{ fontFamily: "monospace" }} />
            </Tooltip>
          ))}
        </Stack>
      ),
    },
  ];
  return (
    <DataTable
      title={t("agents.apps.apps_title")}
      rows={apps}
      columns={columns}
      rowKey={(a) => `${a.source}:${a.name}:${a.version}:${a.last_seen_at}`}
      loading={loading}
    />
  );
}
