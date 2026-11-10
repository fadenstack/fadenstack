/**
 * The Fadenstack apps an administrator offers here (desktop agent, Office
 * add-ins, Nextcloud app, Chrome extension), for anyone signed in: where to
 * get each one and how to connect it to this server. Reached from the chat.
 *
 * Names, descriptions and addresses come from the signed app list: shown as
 * plain text, addresses only as safe links; no picture is loaded from it.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import { appsApi, type OfferedApp, type OfferedApps } from "~/api/apps";
import { errorText } from "~/api/http";
import { CopyButton, ExternalLink, PreviewChip, ReleaseFiles, safeHref } from "~/components/apps/AppParts";
import { UserPageFrame } from "~/components/UserPageFrame";
import { fmtDay } from "~/lib/updateNotice";

export default function MyAppsPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.language;
  const [data, setData] = useState<OfferedApps | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await appsApi.offered(lang));
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [lang]);

  useEffect(() => {
    void load();
  }, [load]);

  let body: React.ReactNode;
  if (loading && !data) {
    body = <CircularProgress aria-label={t("apps.loading")} />;
  } else if (error && !data) {
    body = (
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
    );
  } else if (data) {
    body = (
      <Stack spacing={2}>
        <Stack direction="row" alignItems="center" gap={0.5} flexWrap="wrap">
          <Typography variant="body2" color="text.secondary">
            {t("apps.my.server")}
          </Typography>
          <Typography variant="body2" sx={{ fontFamily: "monospace", wordBreak: "break-all" }} data-testid="apps-server">
            {data.server}
          </Typography>
          <CopyButton text={data.server} label={t("apps.my.copy_address")} />
        </Stack>
        {data.apps.length === 0 ? (
          <Paper variant="outlined" sx={{ p: 3 }} data-testid="apps-empty">
            <Typography color="text.secondary">{t("apps.my.empty")}</Typography>
          </Paper>
        ) : (
          <Stack component="ul" spacing={2} sx={{ m: 0, p: 0, listStyle: "none" }}>
            {data.apps.map((app) => (
              <OfferedAppCard key={app.id} app={app} server={data.server} vendorLinks={data.vendor_links} lang={lang} />
            ))}
          </Stack>
        )}
      </Stack>
    );
  }

  return (
    <UserPageFrame maxWidth={900}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1, flexWrap: "wrap" }}>
        <IconButton onClick={() => navigate("/chat")} aria-label={t("apps.back_to_chat")}>
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h5" component="h1" sx={{ flexGrow: 1 }}>
          {t("apps.title")}
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {data ? t("apps.my.intro_server", { server: data.server }) : t("apps.my.intro")}
      </Typography>
      {body}
    </UserPageFrame>
  );
}

function OfferedAppCard({
  app,
  server,
  vendorLinks,
  lang,
}: {
  app: OfferedApp;
  server: string;
  vendorLinks: boolean;
  lang: string;
}) {
  const { t } = useTranslation();
  const { latest } = app;
  const pageHref = vendorLinks ? safeHref(app.page) : null;
  const storeHref = safeHref(app.store);

  return (
    <Paper component="li" variant="outlined" aria-label={app.name} data-testid={`offered-${app.id}`} sx={{ p: 2 }}>
      <Stack spacing={1.25}>
        <Typography variant="h6" component="h2" sx={{ fontWeight: 700 }}>
          {app.name}
        </Typography>
        {app.description && (
          <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-word" }}>
            {app.description}
          </Typography>
        )}
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
          {latest ? (
            <>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t("apps.latest", { version: latest.version, date: fmtDay(latest.date, lang) })}
              </Typography>
              <PreviewChip channel={latest.channel} />
            </>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {t("updates.apps.no_release")}
            </Typography>
          )}
          {app.supported_until && (
            <Chip size="small" variant="outlined" label={t("apps.supported_until", { date: fmtDay(app.supported_until, lang) })} />
          )}
        </Stack>

        {latest && latest.files.length > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {t("apps.files.title")}
            </Typography>
            <ReleaseFiles files={latest.files} />
          </Box>
        )}

        <Stack direction="row" gap={2} flexWrap="wrap">
          {storeHref && <ExternalLink href={storeHref}>{t("apps.store")}</ExternalLink>}
          {pageHref && <ExternalLink href={pageHref}>{t("apps.app_page")}</ExternalLink>}
        </Stack>

        <Connect app={app} server={server} />
      </Stack>
    </Paper>
  );
}

/** How to connect it to this server, by the kind of app. */
function Connect({ app, server }: { app: OfferedApp; server: string }) {
  const { t } = useTranslation();
  if (!app.connect) return null;
  return (
    <Box>
      <Typography variant="subtitle2" component="h3" sx={{ fontWeight: 700 }}>
        {t("apps.connect.title")}
      </Typography>
      <Typography variant="body2" sx={{ mt: 0.5 }}>
        {t(`apps.connect.${app.connect}`)}
      </Typography>
      {(app.connect === "desktop" || app.connect === "chrome" || app.connect === "office") && (
        <Stack direction="row" alignItems="center" gap={0.5} flexWrap="wrap">
          <Typography variant="body2" color="text.secondary">
            {t("apps.my.server")}
          </Typography>
          <Typography variant="body2" sx={{ fontFamily: "monospace", wordBreak: "break-all" }}>
            {server}
          </Typography>
          <CopyButton text={server} label={t("apps.my.copy_address")} />
        </Stack>
      )}
    </Box>
  );
}
