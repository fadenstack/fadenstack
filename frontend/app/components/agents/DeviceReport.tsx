/**
 * What a device's app last reported about itself: its system and version, the plugins it loaded and the MCP
 * programs it runs, each with the SHA-256 of its file (shortened here, whole in the tooltip and on the copy
 * button), so an administrator can check what runs on the device. Shown on the agent's sign-ins and on My profile.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";

import type { AgentDevice } from "~/api/agents";
import { formatDateTime } from "~/lib/dates";

/** The first 12 characters of a hash and an ellipsis. */
export const shortHash = (sha256: string) => (sha256.length > 12 ? `${sha256.slice(0, 12)}…` : sha256);

/** A hash in monospace: shortened, the whole value in a tooltip, and a button that copies it. */
export function HashValue({ sha256, name }: { sha256: string; name: string }) {
  const { t } = useTranslation();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sha256);
    } catch {
      /* Nothing to copy with; the tooltip shows the whole value. */
    }
  };
  return (
    <Stack direction="row" alignItems="center" spacing={0.25} sx={{ minWidth: 0 }}>
      <Tooltip title={`${t("device_report.sha256")}: ${sha256}`}>
        <Typography variant="caption" fontFamily="monospace" color="text.secondary" data-testid="sha256">
          {shortHash(sha256)}
        </Typography>
      </Tooltip>
      <IconButton size="small" aria-label={t("device_report.copy", { name })} onClick={() => void copy()}>
        <ContentCopyIcon fontSize="inherit" />
      </IconButton>
    </Stack>
  );
}

function Row({ name, detail, sha256 }: { name: string; detail?: string | null; sha256: string }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography variant="body2">{name}</Typography>
      {detail && (
        <Typography variant="body2" color="text.secondary" fontFamily="monospace">
          {detail}
        </Typography>
      )}
      <HashValue sha256={sha256} name={name} />
    </Stack>
  );
}

/** A text button that shows or hides the report under it (the key and the keyboard work as on any button). */
export function DeviceReportToggle({ device }: { device: AgentDevice | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="small" sx={{ p: 0, minWidth: 0 }} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? t("agents.activity.report_hide") : t("agents.activity.report_show")}
      </Button>
      {open && <DeviceReport device={device} />}
    </>
  );
}

export function DeviceReport({ device }: { device: AgentDevice | null }) {
  const { t } = useTranslation();
  if (!device) {
    return (
      <Typography variant="body2" color="text.secondary">
        {t("device_report.not_reported")}
      </Typography>
    );
  }
  const unknown = t("device_report.unknown");
  return (
    <Stack spacing={1} data-testid="device-report">
      <Typography variant="body2">
        <strong>{t("device_report.system")}:</strong> {device.os ?? unknown}
        {" · "}
        <strong>{t("device_report.app_version")}:</strong> {device.app_version ?? unknown}
      </Typography>
      <Stack spacing={0.5}>
        <Typography variant="subtitle2" component="h4">
          {t("device_report.plugins")}
        </Typography>
        {device.plugins.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("device_report.none_plugins")}
          </Typography>
        ) : (
          device.plugins.map((p) => <Row key={`${p.name}:${p.sha256}`} name={p.name} detail={p.version} sha256={p.sha256} />)
        )}
      </Stack>
      <Stack spacing={0.5}>
        <Typography variant="subtitle2" component="h4">
          {t("device_report.mcp_programs")}
        </Typography>
        {device.mcp_programs.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("device_report.none_mcp")}
          </Typography>
        ) : (
          device.mcp_programs.map((m) => (
            <Row key={`${m.name}:${m.sha256}`} name={m.name} detail={m.program} sha256={m.sha256} />
          ))
        )}
      </Stack>
      <Typography variant="caption" color="text.secondary">
        {t("device_report.reported", { when: formatDateTime(device.reported_at) })}
      </Typography>
    </Stack>
  );
}
