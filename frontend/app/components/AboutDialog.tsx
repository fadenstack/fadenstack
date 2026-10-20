/**
 * About Fadenstack: the edition, the release, the licence, and the links people look for -- Help, release notes,
 * security, the developers' docs, third-party notices. Opened from the user menu and from the version line at
 * the bottom of the admin sidebar. Links to fadenstack.com carry `ref=console-about` and the release's first
 * two numbers (lib/vendorLinks.ts); Help and the notices are this server's own pages.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

import { aboutInfo, type AboutInfo } from "~/api/about";

/** This release is a Preview (Help, "Preview releases"); the CLI's release_notice.PREVIEW says the same. */
export const PREVIEW_RELEASE = true;
import BrandMark from "~/components/BrandMark";
import { helpUrl } from "~/lib/pageHelp";
import { DEVELOPERS, EXTERNAL_LINK, SOURCE_CODE, tagVendorUrl, websiteUrl } from "~/lib/vendorLinks";

/** The About facts, once they are there (null while asking, or when the server does not answer). */
export function useAboutInfo(): AboutInfo | null {
  const [info, setInfo] = useState<AboutInfo | null>(null);
  useEffect(() => {
    let live = true;
    aboutInfo()
      .then((value) => live && setInfo(value))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return info;
}

/** The console build the page runs (React Router's manifest version), for "Copy details". */
function consoleBuild(): string {
  const manifest = (globalThis as { __reactRouterManifest?: { version?: string } }).__reactRouterManifest;
  return manifest?.version ?? "";
}

interface AboutLink {
  key: string;
  label: string;
  href: string;
  /** A page of another site: a new tab, without a Referer. */
  external: boolean;
}

export function AboutDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const info = useAboutInfo();
  const [copied, setCopied] = useState(false);
  const version = info?.version ?? "";
  const editionLabel = info
    ? t(`about.edition_${info.edition}`, { defaultValue: info.edition })
    : "";

  const links: AboutLink[] = [
    { key: "help", label: t("about.help"), href: helpUrl(undefined, i18n.language), external: false },
    {
      key: "third-party",
      label: t("about.third_party"),
      href: helpUrl("operate/third-party", i18n.language),
      external: false,
    },
  ];
  if (info?.vendor_links !== false) {
    links.push(
      { key: "releases", label: t("about.release_notes"), href: websiteUrl("/releases/", "console-about", version), external: true },
      { key: "website", label: t("about.website"), href: websiteUrl("/", "console-about", version), external: true },
      { key: "security", label: t("about.security"), href: websiteUrl("/security/", "console-about", version), external: true },
      { key: "developers", label: t("about.developers"), href: tagVendorUrl(`${DEVELOPERS}/`, "console-about", version), external: true },
      { key: "source", label: t("about.source_code"), href: SOURCE_CODE, external: true },
    );
  }

  const details = [
    `${info?.product ?? "Fadenstack"} ${version || t("about.version_source")} (${editionLabel || "core"})`,
    info?.licence ? `${t("about.licence")}: ${info.licence}` : "",
    consoleBuild() ? `${t("about.console_build")}: ${consoleBuild()}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  async function copyDetails() {
    try {
      await navigator.clipboard.writeText(details);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="about-title" data-testid="about-dialog">
      <DialogTitle id="about-title" sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
        <BrandMark size={28} />
        {t("about.title")}
      </DialogTitle>
      <DialogContent>
        <Box component="dl" sx={{ display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 2, rowGap: 0.5, m: 0, mb: 2 }}>
          <Typography component="dt" variant="body2" color="text.secondary">{t("about.edition")}</Typography>
          <Typography component="dd" variant="body2" sx={{ m: 0 }}>{editionLabel}</Typography>
          <Typography component="dt" variant="body2" color="text.secondary">{t("about.version")}</Typography>
          <Typography component="dd" variant="body2" sx={{ m: 0 }} data-testid="about-version">
            {info ? version || t("about.version_source") : ""}
          </Typography>
          {info?.licence && (
            <>
              <Typography component="dt" variant="body2" color="text.secondary">{t("about.licence")}</Typography>
              <Typography component="dd" variant="body2" sx={{ m: 0 }}>{info.licence}</Typography>
            </>
          )}
        </Box>
        {PREVIEW_RELEASE && (
          <Alert severity="info" sx={{ mb: 2 }} data-testid="about-preview">
            <b>{t("about.preview_title")}</b> {t("about.preview_text")}
          </Alert>
        )}
        <Stack component="ul" spacing={0.75} sx={{ listStyle: "none", p: 0, m: 0 }}>
          {links.map((link) => (
            <li key={link.key}>
              <Link
                href={link.href}
                variant="body2"
                data-link={link.key}
                {...(link.external
                  ? { ...EXTERNAL_LINK, "aria-label": `${link.label} ${t("about.new_tab")}` }
                  : { target: "_blank", rel: "noopener" })}
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
              >
                {link.label}
                {link.external && <OpenInNewIcon sx={{ fontSize: 14 }} aria-hidden />}
              </Link>
            </li>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={copyDetails} disabled={!info}>
          {copied ? t("about.copied") : t("about.copy")}
        </Button>
        <Button onClick={onClose} variant="contained">
          {t("about.close")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/**
 * The muted "Fadenstack 0.4.5" at the bottom of the admin sidebar: a click opens About, it never leaves the page
 * (Portainer's pattern).
 */
export function AboutVersionLine() {
  const { t } = useTranslation();
  const info = useAboutInfo();
  const [open, setOpen] = useState(false);
  const label = info?.version ? `Fadenstack ${info.version}` : "Fadenstack";
  return (
    <>
      <Button
        size="small"
        onClick={() => setOpen(true)}
        aria-label={t("about.menu_item")}
        data-testid="about-version-line"
        sx={{
          alignSelf: "flex-start",
          mx: 1.5,
          mb: 0.5,
          px: 1,
          minWidth: 0,
          color: "text.secondary",
          fontSize: "0.75rem",
          fontWeight: 400,
          textTransform: "none",
        }}
      >
        {label}
      </Button>
      <AboutDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}
