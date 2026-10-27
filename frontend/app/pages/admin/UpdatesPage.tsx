/**
 * Updates (administrators): which Fadenstack release this server runs, what is
 * newer, how to upgrade, and what every part of the install runs.
 *
 * Nothing here installs anything. The page shows the commands; an operator
 * runs them on the server, in a window they pick. The release list is read
 * once a day (Settings > Updates), on "Check now", or uploaded by hand on a
 * server without internet.
 */
import { useAboutInfo } from "~/components/AboutDialog";
import { EXTERNAL_LINK, tagVendorUrl } from "~/lib/vendorLinks";
import { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import CheckIcon from "@mui/icons-material/Check";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import RefreshIcon from "@mui/icons-material/Refresh";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import { errorText } from "~/api/http";
import {
  currentRelease,
  updatesApi,
  type InstallPart,
  type PartStatus,
  type ReleaseInfo,
  type UpdatesOverview,
} from "~/api/updates";
import { PageHeader, WidgetCard } from "~/components/dashboard/kit";
import { fmtAgo } from "~/components/dashboard/format";
import { fmtDay, useUpdateNotice } from "~/lib/updateNotice";

/** Where the release list is published; a server without internet gets a copy of it. */
export const FEED_DIRECTORY = "https://fadenstack.com/updates/v1/";
const SETTINGS_PATH = "/admin/settings?tab=general";

type ChipColor = "default" | "success" | "info" | "warning" | "error";

/** The one status the page leads with. */
export function overviewStatus(overview: UpdatesOverview): { key: string; color: ChipColor } {
  if (overview.update_available) {
    if (overview.severity === "critical") return { key: "critical", color: "error" };
    if (overview.severity === "security") return { key: "security", color: "warning" };
    return { key: "update", color: "info" };
  }
  const noList = !overview.latest && !overview.check.feed_generated_at && overview.newer.length === 0;
  if (noList) return { key: "not_checked", color: "default" };
  return { key: "up_to_date", color: "success" };
}

/** On a phone an alert's button goes under its text instead of squeezing it. */
const alertWithActionSx = {
  flexWrap: "wrap",
  "& .MuiAlert-message": { flex: "1 1 200px", minWidth: 0 },
} as const;

const PART_COLORS: Record<PartStatus, ChipColor> = {
  current: "success",
  update: "info",
  mismatch: "warning",
  unknown: "default",
  stopped: "default",
};

export default function UpdatesPage() {
  const { t } = useTranslation();
  // The banner and the sidebar's dot read this; a check here should move them too.
  const notice = useUpdateNotice();
  const [overview, setOverview] = useState<UpdatesOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setOverview(await updatesApi.overview());
    } catch (err: unknown) {
      setLoadError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshNotice = notice.refresh;
  const took = useCallback(
    (next: UpdatesOverview) => {
      setOverview(next);
      setLoadError(null);
      void refreshNotice();
    },
    [refreshNotice],
  );

  async function checkNow() {
    setChecking(true);
    setCheckError(null);
    try {
      // A failed read comes back as the overview with check.error set.
      took(await updatesApi.check());
    } catch (err: unknown) {
      setCheckError(errorText(err));
    } finally {
      setChecking(false);
    }
  }

  const failedWithoutData = Boolean(loadError) && !overview && !loading;

  return (
    <Stack spacing={2} sx={{ pb: 2 }}>
      <PageHeader title={t("updates.title")} subtitle={t("updates.subtitle")} />

      {failedWithoutData && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => void load()}>
              {t("dashboard.retry")}
            </Button>
          }
        >
          {loadError}
        </Alert>
      )}

      {!failedWithoutData && (
        <SummaryCard
          overview={overview}
          loading={loading && !overview}
          checking={checking}
          checkError={checkError}
          onCheck={() => void checkNow()}
        />
      )}

      {overview?.update_available && <UpgradeCard overview={overview} />}

      {overview && overview.newer.length > 0 && <NewerReleasesCard releases={overview.newer} />}

      {!failedWithoutData && <PartsCard overview={overview} loading={loading && !overview} />}

      <OfflineUpload onUploaded={took} />
    </Stack>
  );
}

/* ── This server ──────────────────────────────────────────────────── */

function SummaryCard({
  overview,
  loading,
  checking,
  checkError,
  onCheck,
}: {
  overview: UpdatesOverview | null;
  loading: boolean;
  checking: boolean;
  checkError: string | null;
  onCheck: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const title = overview
    ? t("updates.summary.runs", { version: currentRelease(overview) })
    : t("updates.summary.title");

  return (
    <WidgetCard
      title={title}
      status={{ loading, hasData: Boolean(overview) }}
      actions={
        <Button
          variant="outlined"
          size="small"
          onClick={onCheck}
          disabled={checking}
          startIcon={checking ? <CircularProgress size={14} color="inherit" /> : <RefreshIcon />}
          sx={{ minHeight: 36, whiteSpace: "nowrap" }}
        >
          {checking ? t("updates.summary.checking") : t("updates.summary.check_now")}
        </Button>
      }
      skeleton={
        <Stack gap={1}>
          <Skeleton variant="rounded" height={28} width={160} />
          <Skeleton variant="rounded" height={40} />
          <Skeleton variant="rounded" height={20} width="70%" />
        </Stack>
      }
    >
      {overview && <SummaryBody overview={overview} lang={lang} checkError={checkError} />}
    </WidgetCard>
  );
}

function SummaryBody({
  overview,
  lang,
  checkError,
}: {
  overview: UpdatesOverview;
  lang: string;
  checkError: string | null;
}) {
  const { t } = useTranslation();
  const { check } = overview;
  const status = overviewStatus(overview);
  const latest = overview.latest_release;
  const current = currentRelease(overview);

  const meta: string[] = [
    t("updates.summary.channel", {
      channel: t(`updates.channel.${check.channel}`, { defaultValue: check.channel }),
    }),
    check.checked_at
      ? t("updates.summary.checked", { ago: fmtAgo(check.checked_at, lang) })
      : t("updates.summary.never_checked"),
  ];
  if (check.succeeded_at && check.checked_at && check.succeeded_at !== check.checked_at) {
    meta.push(t("updates.summary.last_success", { ago: fmtAgo(check.succeeded_at, lang) }));
  }
  if (check.feed_generated_at) {
    const date = fmtDay(check.feed_generated_at, lang);
    meta.push(
      check.source === "upload"
        ? t("updates.summary.feed_uploaded", { date })
        : t("updates.summary.feed_date", { date }),
    );
  }
  if (overview.supported_until && !overview.unsupported) {
    meta.push(t("updates.summary.supported_until", { date: fmtDay(overview.supported_until, lang) }));
  }

  const settingsLink = (
    <Button component={RouterLink} to={SETTINGS_PATH} color="inherit" size="small" sx={{ whiteSpace: "nowrap" }}>
      {t("updates.summary.open_settings")}
    </Button>
  );

  return (
    <Stack spacing={1.5}>
      <Stack direction="row" gap={1} flexWrap="wrap" alignItems="center">
        <Chip size="small" color={status.color} label={t(`updates.status.${status.key}`)} data-testid="update-status" />
        {overview.unsupported && <Chip size="small" color="error" label={t("updates.status.unsupported")} />}
      </Stack>

      {latest && (
        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {t("updates.summary.newest", { version: latest.version, date: fmtDay(latest.date, lang) })}
          </Typography>
          {latest.summary && (
            <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-word" }}>
              {latest.summary}
            </Typography>
          )}
          {latest.notes_url && <NotesLink url={latest.notes_url} />}
        </Box>
      )}

      <Typography variant="caption" color="text.secondary">
        {meta.join(" · ")}
      </Typography>

      {overview.unsupported && (
        <Alert severity="warning">
          {overview.supported_until
            ? t("updates.summary.unsupported", { version: current, date: fmtDay(overview.supported_until, lang) })
            : t("updates.summary.unsupported_plain", { version: current })}
        </Alert>
      )}
      {!check.enabled && (
        <Alert severity="info" action={settingsLink} sx={alertWithActionSx} data-testid="check-off">
          {t("updates.summary.check_off")}
        </Alert>
      )}
      {check.error && (
        <Alert
          severity={check.failing ? "warning" : "info"}
          action={check.failing ? settingsLink : undefined}
          sx={alertWithActionSx}
          data-testid="check-error"
        >
          {t("updates.summary.check_error", { error: check.error })}
          {check.failing && (
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              {t("updates.summary.failing_hint")}
            </Typography>
          )}
        </Alert>
      )}
      {checkError && <Alert severity="error">{checkError}</Alert>}
    </Stack>
  );
}

/**
 * A release's notes; on fadenstack.com tagged with ref=console-updates and this server's release line. Not
 * where the server switched the vendor's links off.
 */
function NotesLink({ url }: { url: string }) {
  const { t } = useTranslation();
  const about = useAboutInfo();
  if (about?.vendor_links === false) return null;
  return (
    <Link
      href={tagVendorUrl(url, "console-updates", about?.version)}
      {...EXTERNAL_LINK}
      aria-label={`${t("updates.release_notes")} ${t("about.new_tab")}`}
      variant="body2"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, mt: 0.5 }}
    >
      {t("updates.release_notes")}
      <OpenInNewIcon sx={{ fontSize: 14 }} aria-hidden />
    </Link>
  );
}

/* ── How to upgrade ───────────────────────────────────────────────── */

function UpgradeCard({ overview }: { overview: UpdatesOverview }) {
  const { t } = useTranslation();
  const steps = [currentRelease(overview), ...overview.path];
  return (
    <WidgetCard title={t("updates.upgrade.title")}>
      <Stack spacing={1.5}>
        {overview.path.length > 1 && (
          <Alert severity="info" data-testid="upgrade-steps">
            {t("updates.upgrade.steps", { steps: steps.join(" → "), next: overview.path[0] })}
          </Alert>
        )}
        <Typography variant="body2">{t("updates.upgrade.where")}</Typography>
        {overview.commands.length > 0 && <CommandBlock commands={overview.commands} />}
        <Typography variant="body2" color="text.secondary">
          {t("updates.upgrade.maintenance")}
        </Typography>
        {overview.released_agent && (
          <Typography variant="body2" color="text.secondary">
            {t("updates.upgrade.agents", { version: overview.released_agent })}{" "}
            <Link component={RouterLink} to="/admin/nodes">
              {t("updates.upgrade.agents_link")}
            </Link>
          </Typography>
        )}
      </Stack>
    </WidgetCard>
  );
}

function CommandBlock({ commands }: { commands: string[] }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const text = commands.join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // The commands are on screen and can be selected by hand.
      setCopied(false);
    }
  }

  return (
    <Box sx={{ position: "relative", bgcolor: "action.hover", borderRadius: 1, p: 1.5, pr: 6 }}>
      <Box
        component="pre"
        data-testid="upgrade-commands"
        // Wrapped at spaces; a word longer than the line (a long URL) breaks where it must.
        sx={{ fontFamily: "monospace", fontSize: "0.8rem", m: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
      >
        {text}
      </Box>
      <Tooltip title={copied ? t("updates.upgrade.copied") : t("updates.upgrade.copy")}>
        <IconButton
          size="small"
          onClick={() => void copy()}
          aria-label={t("updates.upgrade.copy")}
          sx={{ position: "absolute", top: 4, right: 4, width: 40, height: 40 }}
        >
          {copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
    </Box>
  );
}

/* ── Newer releases ───────────────────────────────────────────────── */

function NewerReleasesCard({ releases }: { releases: ReleaseInfo[] }) {
  const { t } = useTranslation();
  return (
    <WidgetCard title={t("updates.newer.title")}>
      <Stack component="ul" spacing={1} sx={{ m: 0, p: 0, listStyle: "none" }}>
        {releases.map((release) => (
          <ReleaseItem key={release.version} release={release} />
        ))}
      </Stack>
    </WidgetCard>
  );
}

function severityColor(severity: string): ChipColor {
  if (severity === "critical") return "error";
  if (severity === "security") return "warning";
  return "default";
}

function ReleaseItem({ release }: { release: ReleaseInfo }) {
  const { t, i18n } = useTranslation();
  return (
    <Box
      component="li"
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5, bgcolor: "dashboard.surfaceSubtle" }}
    >
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <Typography variant="subtitle2" component="h3" sx={{ fontWeight: 700 }}>
          {release.version}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {fmtDay(release.date, i18n.language)}
        </Typography>
        <Chip
          size="small"
          variant={release.severity === "normal" ? "outlined" : "filled"}
          color={severityColor(release.severity)}
          label={t(`updates.severity.${release.severity}`, { defaultValue: release.severity })}
        />
        {release.channel === "preview" && <Chip size="small" color="secondary" label={t("updates.channel.preview")} />}
      </Stack>
      {release.summary && (
        <Typography variant="body2" sx={{ mt: 0.75, wordBreak: "break-word" }}>
          {release.summary}
        </Typography>
      )}
      {release.fixes.length > 0 && (
        <Box component="ul" sx={{ m: 0, mt: 0.75, pl: 2.5 }}>
          {release.fixes.map((fix) => (
            <Typography component="li" variant="body2" key={fix.id || fix.summary} sx={{ wordBreak: "break-word" }}>
              {fix.id && (
                <Box component="span" sx={{ fontFamily: "monospace", fontWeight: 600 }}>
                  {fix.id}
                </Box>
              )}
              {fix.id && fix.severity ? ` (${fix.severity})` : ""}
              {fix.id ? ": " : ""}
              {fix.summary}
            </Typography>
          ))}
        </Box>
      )}
      {release.notes_url && <NotesLink url={release.notes_url} />}
    </Box>
  );
}

/* ── Parts of the install ─────────────────────────────────────────── */

function PartsCard({ overview, loading }: { overview: UpdatesOverview | null; loading: boolean }) {
  const { t } = useTranslation();
  const parts = overview?.parts ?? [];
  return (
    <WidgetCard
      title={t("updates.parts.title")}
      subtitle={t("updates.parts.subtitle")}
      status={{ loading, hasData: Boolean(overview) }}
    >
      {overview?.docker_error && (
        <Alert severity="warning" sx={{ mb: 1.5 }}>
          {overview.docker_error}
        </Alert>
      )}
      {parts.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("updates.parts.empty")}
        </Typography>
      ) : (
        // Wider than a phone: the table scrolls sideways inside the card.
        <TableContainer sx={{ overflowX: "auto" }}>
          <Table size="small" sx={{ minWidth: 720, "& .MuiTableCell-head": { whiteSpace: "nowrap" } }}>
            <TableHead>
              <TableRow>
                <TableCell>{t("updates.parts.col_part")}</TableCell>
                <TableCell>{t("updates.parts.col_name")}</TableCell>
                <TableCell>{t("updates.parts.col_running")}</TableCell>
                <TableCell>{t("updates.parts.col_expected")}</TableCell>
                <TableCell>{t("updates.parts.col_status")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {parts.map((part) => (
                <PartRow key={`${part.kind}:${part.name}`} part={part} />
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </WidgetCard>
  );
}

/** A cluster's runtime is a long image name: wrapped, but never squeezed to a few letters a line. */
const versionCell = { fontFamily: "monospace", fontSize: "0.8rem", wordBreak: "break-all", minWidth: 150, maxWidth: 260 } as const;

function PartRow({ part }: { part: InstallPart }) {
  const { t } = useTranslation();
  return (
    <TableRow>
      <TableCell sx={{ whiteSpace: "nowrap" }}>{t(`updates.parts.kind.${part.kind}`, { defaultValue: part.kind })}</TableCell>
      <TableCell sx={{ minWidth: 140 }}>
        {part.link ? (
          <Link component={RouterLink} to={part.link} variant="body2" sx={{ fontWeight: 600 }}>
            {part.name}
          </Link>
        ) : (
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {part.name}
          </Typography>
        )}
        {part.detail && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", wordBreak: "break-all" }}>
            {part.detail}
          </Typography>
        )}
      </TableCell>
      <TableCell sx={versionCell}>{part.running ?? "–"}</TableCell>
      <TableCell sx={versionCell}>{part.expected ?? "–"}</TableCell>
      <TableCell>
        <Chip
          size="small"
          color={PART_COLORS[part.status] ?? "default"}
          variant={part.status === "current" ? "outlined" : "filled"}
          label={t(`updates.parts.status.${part.status}`, { defaultValue: part.status })}
        />
      </TableCell>
    </TableRow>
  );
}

/* ── No internet: upload the release list ─────────────────────────── */

function FilePick({
  label,
  accept,
  file,
  onPick,
  disabled,
}: {
  label: string;
  accept: string;
  file: File | null;
  onPick: (file: File | null) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
      <Button component="label" variant="outlined" size="small" disabled={disabled} sx={{ minHeight: 36 }}>
        {label}
        <input
          hidden
          type="file"
          accept={accept}
          aria-label={label}
          onChange={(event) => onPick(event.target.files?.[0] ?? null)}
        />
      </Button>
      <Typography variant="body2" color={file ? "text.primary" : "text.secondary"} sx={{ wordBreak: "break-all" }}>
        {file ? file.name : t("updates.offline.no_file")}
      </Typography>
    </Stack>
  );
}

function OfflineUpload({ onUploaded }: { onUploaded: (overview: UpdatesOverview) => void }) {
  const { t } = useTranslation();
  const [index, setIndex] = useState<File | null>(null);
  const [signature, setSignature] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function upload() {
    if (!index || !signature) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      onUploaded(await updatesApi.uploadFeed(index, signature));
      setDone(true);
    } catch (err: unknown) {
      // 422: the server says why (a bad signature, not a release list, ...).
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Accordion variant="outlined" disableGutters slotProps={{ transition: { unmountOnExit: true } }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="subtitle2">{t("updates.offline.title")}</Typography>
      </AccordionSummary>
      <AccordionDetails>
        <Stack spacing={1.5}>
          <Typography variant="body2">{t("updates.offline.body")}</Typography>
          <Typography variant="body2">
            <Link href={FEED_DIRECTORY} target="_blank" rel="noreferrer" sx={{ wordBreak: "break-all" }}>
              {FEED_DIRECTORY}
            </Link>
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t("updates.offline.mirror")}{" "}
            <Link component={RouterLink} to={SETTINGS_PATH}>
              {t("updates.summary.open_settings")}
            </Link>
          </Typography>
          <FilePick
            label={t("updates.offline.choose_index")}
            accept=".json,application/json"
            file={index}
            onPick={setIndex}
            disabled={busy}
          />
          <FilePick
            label={t("updates.offline.choose_signature")}
            accept=".sig"
            file={signature}
            onPick={setSignature}
            disabled={busy}
          />
          <Box>
            <Button
              variant="contained"
              startIcon={busy ? <CircularProgress size={14} color="inherit" /> : <UploadFileIcon />}
              disabled={!index || !signature || busy}
              onClick={() => void upload()}
            >
              {busy ? t("updates.offline.uploading") : t("updates.offline.upload")}
            </Button>
          </Box>
          {error && (
            <Alert severity="error" data-testid="upload-error">
              {error}
            </Alert>
          )}
          {done && <Alert severity="success">{t("updates.offline.uploaded")}</Alert>}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}
