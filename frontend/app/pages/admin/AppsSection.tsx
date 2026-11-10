/**
 * The Apps section of the Updates page (administrators): the Fadenstack apps
 * (desktop agent, Office add-ins, Nextcloud app, Chrome extension) with their
 * releases, which versions run here, and which apps are offered to people on
 * their Apps page. Nothing is installed from here.
 *
 * The app list is signed like the release list: read once a day, on "Check
 * now", or uploaded by hand. Everything the list says (names, summaries,
 * fixes) is shown as plain text; addresses only as safe links.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link as RouterLink, useLocation } from "react-router";
import { useTranslation } from "react-i18next";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";

import CheckIcon from "@mui/icons-material/Check";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DownloadIcon from "@mui/icons-material/Download";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import RefreshIcon from "@mui/icons-material/Refresh";
import UploadFileIcon from "@mui/icons-material/UploadFile";

import { errorText } from "~/api/http";
import {
  connectFileUrl,
  updatesApi,
  type AppEntry,
  type AppsOverview,
  type ConnectFileKind,
} from "~/api/updates";
import { ExternalLink, PreviewChip, ReleaseFiles, safeHref } from "~/components/apps/AppParts";
import { WidgetCard } from "~/components/dashboard/kit";
import { fmtAgo } from "~/components/dashboard/format";
import { fmtDay } from "~/lib/updateNotice";

const SETTINGS_PATH = "/admin/settings?tab=general";
/** Where this server's own certificate is served; Office needs it when the server has its own CA. */
const CA_PATH = "/install/ca.crt";

const alertWithActionSx = {
  flexWrap: "wrap",
  "& .MuiAlert-message": { flex: "1 1 200px", minWidth: 0 },
} as const;

export default function AppsSection({
  checkTick,
  checking,
  onCheck,
}: {
  /** Counts up after the page has read the lists: the apps are fetched again. */
  checkTick: number;
  checking: boolean;
  onCheck: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const location = useLocation();
  const [data, setData] = useState<AppsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const anchor = useRef<HTMLDivElement | null>(null);
  const scrolled = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await updatesApi.apps(lang));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [lang]);

  useEffect(() => {
    void load();
    // checkTick: the page just read the lists.
  }, [load, checkTick]);

  // The dashboard links to /admin/updates#apps: scroll there once the section has its content.
  useEffect(() => {
    if (scrolled.current || loading || location.hash !== "#apps") return;
    scrolled.current = true;
    anchor.current?.scrollIntoView?.({ block: "start" });
  }, [loading, location.hash]);

  const failedWithoutData = Boolean(error) && !data && !loading;

  return (
    <Box id="apps" ref={anchor} sx={{ scrollMarginTop: 16 }}>
      <WidgetCard
        title={t("updates.apps.title")}
        subtitle={t("updates.apps.subtitle")}
        status={{ loading: loading && !data, hasData: Boolean(data) }}
        actions={
          <Button
            variant="outlined"
            size="small"
            onClick={onCheck}
            disabled={checking}
            startIcon={checking ? <CircularProgress size={14} color="inherit" /> : <RefreshIcon />}
            sx={{ minHeight: 36, whiteSpace: "nowrap" }}
            aria-label={t("updates.apps.check_now_label")}
            data-testid="apps-check-now"
          >
            {checking ? t("updates.summary.checking") : t("updates.summary.check_now")}
          </Button>
        }
        skeleton={
          <Stack gap={1}>
            <Skeleton variant="rounded" height={24} width="50%" />
            <Skeleton variant="rounded" height={80} />
          </Stack>
        }
      >
        <Stack spacing={2}>
          {failedWithoutData && (
            <Alert
              severity="error"
              action={
                <Button color="inherit" size="small" onClick={() => void load()}>
                  {t("dashboard.retry")}
                </Button>
              }
            >
              {error}
            </Alert>
          )}
          {data && (
            <>
              <ListStatus data={data} lang={lang} />
              {!data.news && (
                <Alert
                  severity="info"
                  sx={alertWithActionSx}
                  data-testid="apps-news-off"
                  action={
                    <Button component={RouterLink} to={SETTINGS_PATH} color="inherit" size="small">
                      {t("updates.summary.open_settings")}
                    </Button>
                  }
                >
                  {t("updates.apps.news_off")}
                </Alert>
              )}
              {!data.in_use_known && (
                <Alert severity="info" data-testid="apps-in-use-unknown">
                  {t("updates.apps.in_use_unknown")}
                </Alert>
              )}
              {data.apps.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t("updates.apps.empty")}
                </Typography>
              ) : (
                <Stack component="ul" spacing={1.5} sx={{ m: 0, p: 0, listStyle: "none" }}>
                  {data.apps.map((app) => (
                    <AppItem key={app.id} app={app} data={data} lang={lang} onChanged={setData} />
                  ))}
                </Stack>
              )}
            </>
          )}
          <AppsUpload lang={lang} onUploaded={setData} />
        </Stack>
      </WidgetCard>
    </Box>
  );
}

/* ── The app list itself ──────────────────────────────────────────── */

function ListStatus({ data, lang }: { data: AppsOverview; lang: string }) {
  const { t } = useTranslation();
  const { check } = data;
  const meta: string[] = [
    check.checked_at ? t("updates.apps.checked", { ago: fmtAgo(check.checked_at, lang) }) : t("updates.apps.never_checked"),
  ];
  if (check.list_generated_at) {
    const date = fmtDay(check.list_generated_at, lang);
    meta.push(check.source === "upload" ? t("updates.apps.list_uploaded", { date }) : t("updates.apps.list_date", { date }));
  }
  if (check.expires && !check.expired) {
    meta.push(t("updates.apps.valid_until", { date: fmtDay(check.expires, lang) }));
  }
  return (
    <Stack spacing={1}>
      {check.expired && (
        <Alert severity="warning" data-testid="apps-expired">
          {t("updates.apps.expired", { date: fmtDay(check.expires, lang) })}
        </Alert>
      )}
      <Typography variant="caption" color="text.secondary" data-testid="apps-status">
        {meta.join(" · ")}
      </Typography>
      {check.error && (
        <Alert severity="warning" data-testid="apps-check-error">
          {t("updates.apps.check_error", { error: check.error })}
        </Alert>
      )}
    </Stack>
  );
}

/* ── One app ──────────────────────────────────────────────────────── */

function severityColor(severity: string): "default" | "warning" | "error" {
  if (severity === "critical") return "error";
  if (severity === "security") return "warning";
  return "default";
}

function AppItem({
  app,
  data,
  lang,
  onChanged,
}: {
  app: AppEntry;
  data: AppsOverview;
  lang: string;
  onChanged: (next: AppsOverview) => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connect, setConnect] = useState<ConnectFileKind | null>(null);
  const { latest } = app;
  const urgent = app.severity === "security" || app.severity === "critical";
  const pageHref = data.vendor_links ? safeHref(app.page) : null;
  const storeHref = safeHref(app.store);
  const notesHref = latest ? safeHref(latest.notes_url) : null;

  async function offer(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await updatesApi.setAppOffered(app.id, next, lang));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Box
      component="li"
      aria-label={app.name}
      data-testid={`app-${app.id}`}
      sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5, bgcolor: "dashboard.surfaceSubtle" }}
    >
      <Stack spacing={1}>
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap" justifyContent="space-between">
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography variant="subtitle1" component="h3" sx={{ fontWeight: 700 }}>
              {app.name}
            </Typography>
            {app.new && <Chip size="small" color="info" label={t("apps.new")} />}
          </Stack>
          <FormControlLabel
            sx={{ m: 0 }}
            control={
              <Switch
                checked={app.offered}
                disabled={busy}
                onChange={(event) => void offer(event.target.checked)}
                slotProps={{ input: { "aria-label": `${t("updates.apps.offer")}: ${app.name}` } }}
              />
            }
            label={t("updates.apps.offer")}
          />
        </Stack>
        {error && <Alert severity="error">{error}</Alert>}

        {app.description && (
          <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-word" }}>
            {app.description}
          </Typography>
        )}

        {latest ? (
          <Box>
            <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t("apps.latest", { version: latest.version, date: fmtDay(latest.date, lang) })}
              </Typography>
              <PreviewChip channel={latest.channel} />
              {(latest.severity === "security" || latest.severity === "critical") && (
                <Chip
                  size="small"
                  color={severityColor(latest.severity)}
                  label={t(`updates.severity.${latest.severity}`)}
                />
              )}
            </Stack>
            {latest.summary && (
              <Typography variant="body2" sx={{ mt: 0.5, wordBreak: "break-word" }}>
                {latest.summary}
              </Typography>
            )}
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            {t("updates.apps.no_release")}
          </Typography>
        )}

        {urgent && app.outdated.length > 0 && latest && (
          <Alert severity={app.severity === "critical" ? "error" : "warning"} data-testid={`app-alert-${app.id}`}>
            {t("updates.apps.security_alert", { version: latest.version })}
          </Alert>
        )}

        <Box>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {t("updates.apps.in_use")}
          </Typography>
          {app.in_use.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("updates.apps.in_use_none", { days: data.in_use_days })}
            </Typography>
          ) : (
            <Stack component="ul" direction="row" gap={1} flexWrap="wrap" sx={{ m: 0, p: 0, listStyle: "none" }}>
              {app.in_use.map((use) => {
                const outdated = app.outdated.includes(use.version);
                return (
                  <Box component="li" key={use.version}>
                    <Chip
                      size="small"
                      variant={outdated ? "filled" : "outlined"}
                      color={outdated ? "warning" : "default"}
                      label={`${use.version} · ${t("updates.apps.last_seen", { ago: fmtAgo(use.last_seen, lang) })}${
                        outdated ? ` · ${t("updates.apps.outdated")}` : ""
                      }`}
                    />
                  </Box>
                );
              })}
            </Stack>
          )}
        </Box>

        {app.unsupported.length > 0 && (
          <Typography variant="body2" color="warning.main">
            {t("updates.apps.unsupported", { versions: app.unsupported.join(", ") })}
          </Typography>
        )}
        {app.supported_until && (
          <Typography variant="body2" color="text.secondary">
            {t("apps.supported_until", { date: fmtDay(app.supported_until, lang) })}
          </Typography>
        )}
        {app.server_too_old && latest?.requires_server && (
          <Alert severity="warning" data-testid={`app-server-old-${app.id}`}>
            {t("updates.apps.server_too_old", { version: latest.requires_server })}
          </Alert>
        )}

        <Stack direction="row" gap={2} flexWrap="wrap">
          {notesHref && <ExternalLink href={notesHref}>{t("apps.release_notes")}</ExternalLink>}
          {pageHref && <ExternalLink href={pageHref}>{t("apps.app_page")}</ExternalLink>}
          {storeHref && <ExternalLink href={storeHref}>{t("apps.store")}</ExternalLink>}
        </Stack>

        {latest && latest.files.length > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {t("apps.files.title")}
            </Typography>
            <ReleaseFiles files={latest.files} />
          </Box>
        )}

        {app.connect_files.length > 0 && (
          <Stack direction="row" gap={1} flexWrap="wrap">
            {app.connect_files.map((kind) => (
              <Button key={kind} variant="outlined" size="small" onClick={() => setConnect(kind)} sx={{ minHeight: 36 }}>
                {t(`updates.apps.connect.${kind}.button`)}
              </Button>
            ))}
          </Stack>
        )}
      </Stack>
      {connect && (
        <ConnectDialog app={app} kind={connect} ownCa={data.own_ca} onClose={() => setConnect(null)} />
      )}
    </Box>
  );
}

/* ── Connect files ────────────────────────────────────────────────── */

function ConnectDialog({
  app,
  kind,
  ownCa,
  onClose,
}: {
  app: AppEntry;
  kind: ConnectFileKind;
  ownCa: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    updatesApi
      .connectFile(app.id, kind)
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [app.id, kind]);

  async function copy() {
    if (text === null) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // The text is on screen and can be selected by hand.
      setCopied(false);
    }
  }

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md" aria-labelledby={`connect-title-${app.id}`}>
      <DialogTitle id={`connect-title-${app.id}`}>
        {t(`updates.apps.connect.${kind}.title`, { app: app.name })}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography variant="body2">{t(`updates.apps.connect.${kind}.steps`)}</Typography>
          {kind === "office-policy" && ownCa && (
            <Typography variant="body2" data-testid="connect-ca">
              {t("updates.apps.connect.office-policy.ca")}{" "}
              <Link href={CA_PATH} download>
                {t("updates.apps.connect.office-policy.ca_link")}
              </Link>
            </Typography>
          )}
          <Typography variant="body2" color="text.secondary">
            {t("updates.apps.connect.no_secrets")}
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          {!error && text === null && <CircularProgress size={24} />}
          {text !== null && (
            <Box
              component="pre"
              data-testid="connect-file"
              sx={{
                fontFamily: "monospace",
                fontSize: "0.8rem",
                m: 0,
                p: 1.5,
                bgcolor: "action.hover",
                borderRadius: 1,
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                maxHeight: 360,
                overflow: "auto",
              }}
            >
              {text}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ flexWrap: "wrap" }}>
        <Button
          onClick={() => void copy()}
          disabled={text === null}
          startIcon={copied ? <CheckIcon /> : <ContentCopyIcon />}
        >
          {copied ? t("apps.copied") : t("apps.copy")}
        </Button>
        <Button component="a" href={connectFileUrl(app.id, kind)} download startIcon={<DownloadIcon />}>
          {t("updates.apps.connect.download")}
        </Button>
        <Button onClick={onClose}>{t("updates.apps.connect.close")}</Button>
      </DialogActions>
    </Dialog>
  );
}

/* ── No internet: upload the app list ─────────────────────────────── */

function PickFile({
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

function AppsUpload({ lang, onUploaded }: { lang: string; onUploaded: (next: AppsOverview) => void }) {
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
      onUploaded(await updatesApi.uploadAppFeed(index, signature, lang));
      setDone(true);
    } catch (err: unknown) {
      // 422: the server says why (a bad signature, not an app list, ...).
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Accordion variant="outlined" disableGutters slotProps={{ transition: { unmountOnExit: true } }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="subtitle2">{t("updates.apps.upload.title")}</Typography>
      </AccordionSummary>
      <AccordionDetails>
        <Stack spacing={1.5}>
          <Typography variant="body2">{t("updates.apps.upload.body")}</Typography>
          <PickFile
            label={t("updates.apps.upload.choose_index")}
            accept=".json,application/json"
            file={index}
            onPick={setIndex}
            disabled={busy}
          />
          <PickFile
            label={t("updates.apps.upload.choose_signature")}
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
              {busy ? t("updates.offline.uploading") : t("updates.apps.upload.upload")}
            </Button>
          </Box>
          {error && (
            <Alert severity="error" data-testid="apps-upload-error">
              {error}
            </Alert>
          )}
          {done && <Alert severity="success">{t("updates.apps.upload.uploaded")}</Alert>}
        </Stack>
      </AccordionDetails>
    </Accordion>
  );
}
