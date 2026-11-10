/**
 * Pieces the administrators' Apps section and the people's Apps page share.
 *
 * What comes from the app list (names, summaries, addresses, hashes) is shown
 * as plain React text. Addresses become links only when they are http(s), and
 * every outgoing link opens in a new tab with no opener and no Referer. No
 * picture from the list is ever loaded.
 */
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import CheckIcon from "@mui/icons-material/Check";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

import type { AppReleaseFile } from "~/api/updates";
import { fmtBytes } from "~/components/dashboard/format";

/** *url* when it is an http(s) address, otherwise null: it is then shown as text, not as a link. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

/** The first characters of a hash, enough to tell two apart. */
export function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : hash;
}

/** A link to another site: new tab, no opener, no Referer. */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  const { t } = useTranslation();
  const safe = safeHref(href);
  if (!safe) {
    return (
      <Typography component="span" variant="body2" sx={{ wordBreak: "break-all" }}>
        {children}
      </Typography>
    );
  }
  return (
    <Link
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      variant="body2"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, wordBreak: "break-word" }}
    >
      {children}
      <OpenInNewIcon sx={{ fontSize: 14 }} titleAccess={t("apps.new_tab")} />
    </Link>
  );
}

/** Copies *text*; the check mark shows for two seconds. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  async function copy() {
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
    <Tooltip title={copied ? t("apps.copied") : label}>
      <IconButton size="small" onClick={() => void copy()} aria-label={label} sx={{ width: 40, height: 40 }}>
        {copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
      </IconButton>
    </Tooltip>
  );
}

/** The version's date, a "Preview" chip for the preview channel. */
export function PreviewChip({ channel }: { channel: string }) {
  const { t } = useTranslation();
  return channel === "preview" ? <Chip size="small" color="secondary" label={t("apps.preview")} /> : null;
}

/** The download files of a release: platform and kind, size, SHA-256 with a copy button, the link. */
export function ReleaseFiles({ files }: { files: AppReleaseFile[] }) {
  const { t, i18n } = useTranslation();
  if (files.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        {t("apps.files.none")}
      </Typography>
    );
  }
  return (
    <Stack component="ul" spacing={1} sx={{ m: 0, p: 0, listStyle: "none" }} aria-label={t("apps.files.title")}>
      {files.map((file) => (
        <Box
          component="li"
          key={`${file.platform}:${file.kind}:${file.url}`}
          sx={{ border: 1, borderColor: "divider", borderRadius: 1, px: 1.5, py: 1 }}
        >
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {`${file.platform} · ${file.kind}`}
            </Typography>
            {file.size != null && (
              <Typography variant="caption" color="text.secondary">
                {fmtBytes(file.size, i18n.language)}
              </Typography>
            )}
            <ExternalLink href={file.url}>{t("apps.files.download")}</ExternalLink>
          </Stack>
          {file.sha256 && (
            <Stack direction="row" alignItems="center" gap={0.5}>
              <Typography variant="caption" color="text.secondary">
                {t("apps.files.sha256")}
              </Typography>
              <Typography
                variant="caption"
                title={file.sha256}
                sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
              >
                {shortHash(file.sha256)}
              </Typography>
              <CopyButton text={file.sha256} label={t("apps.files.copy_hash")} />
            </Stack>
          )}
        </Box>
      ))}
    </Stack>
  );
}
