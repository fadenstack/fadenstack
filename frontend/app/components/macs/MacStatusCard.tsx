/**
 * What an operator should know about a Mac before it serves models: whether
 * the native runtime is there, the sandbox, the application firewall (it can
 * block the gateway) and FileVault (an unattended reboot needs authrestart).
 * Read from the agent's reported capabilities; anything it did not report
 * says so instead of guessing.
 *
 * One row of status chips; the full sentences sit in a tooltip on each chip
 * and under "Details". Only a state that needs action (sandbox off, firewall
 * on) is spelled out in a warning that stays visible.
 */
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import Alert from "@mui/material/Alert";
import Chip from "@mui/material/Chip";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatBytes } from "~/lib/engine";


export interface MacStatus {
  filevault?: boolean | null;
  firewall?: { enabled?: boolean | null; stealth?: boolean | null; block_all?: boolean | null } | null;
}

type Tone = "ok" | "warn" | "neutral";

interface Item {
  id: string;
  tone: Tone;
  chip: string;
  text: string;
  /** Needs the operator to act: spelled out in a warning that stays visible. */
  action?: boolean;
}

function StatusChip({ item }: { item: Item }) {
  const icon =
    item.tone === "ok" ? (
      <CheckCircleOutlineIcon />
    ) : item.tone === "warn" ? (
      <WarningAmberIcon />
    ) : (
      <HelpOutlineIcon />
    );
  return (
    <Tooltip title={item.text} arrow>
      <Chip
        size="small"
        variant="outlined"
        icon={icon}
        label={item.chip}
        color={item.tone === "ok" ? "success" : item.tone === "warn" ? "warning" : "default"}
        tabIndex={0}
        // A chip is not interactive: it reads as one image carrying the whole sentence, and shows it on focus.
        role="img"
        data-testid={`mac-${item.id}`}
        data-tone={item.tone}
        aria-label={`${item.chip}. ${item.text}`}
      />
    </Tooltip>
  );
}

export function MacStatusCard({
  capabilities,
  action,
  name,
}: {
  capabilities: Record<string, unknown> | null | undefined;
  /** The machine's name. The console is rarely on the Mac, so it is never "this" Mac. */
  name?: string;
  /** The Mac's main action (serve), shown at the end of the row. */
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const caps = capabilities ?? {};
  const status = (caps.mac_status ?? {}) as MacStatus;
  // The agent reports the working set only once a runtime is staged.
  const workingSet = Number(
    (caps.metal as Record<string, unknown> | undefined)?.recommended_max_working_set_bytes ?? 0,
  );
  const staged = workingSet > 0;
  const sandbox = caps.native_sandbox;
  const firewall = status.firewall?.enabled ?? null;
  const vault = status.filevault ?? null;

  const items: Item[] = [
    {
      id: "runtime",
      tone: staged ? "ok" : "neutral",
      chip: staged ? t("mac.chip_runtime_ready") : t("mac.chip_runtime_missing"),
      text: staged ? t("mac.runtime_ready") : t("mac.runtime_missing"),
    },
    {
      id: "sandbox",
      tone: sandbox === true ? "ok" : sandbox === false ? "warn" : "neutral",
      chip: sandbox === true ? t("mac.chip_sandbox_on") : sandbox === false ? t("mac.chip_sandbox_off") : t("mac.chip_sandbox_unknown"),
      text: sandbox === true ? t("mac.sandbox_on") : sandbox === false ? t("mac.sandbox_off") : t("mac.sandbox_unknown"),
      action: sandbox === false,
    },
    {
      id: "firewall",
      tone: firewall === true ? "warn" : firewall === false ? "ok" : "neutral",
      chip: firewall === true ? t("mac.chip_firewall_on") : firewall === false ? t("mac.chip_firewall_off") : t("mac.chip_firewall_unknown"),
      text: firewall === true ? t("mac.firewall_on") : firewall === false ? t("mac.firewall_off") : t("mac.firewall_unknown"),
      action: firewall === true,
    },
    {
      id: "filevault",
      tone: "neutral",
      chip: vault === true ? t("mac.chip_filevault_on") : vault === false ? t("mac.chip_filevault_off") : t("mac.chip_filevault_unknown"),
      text: vault === true ? t("mac.filevault_on") : vault === false ? t("mac.filevault_off") : t("mac.filevault_unknown"),
    },
  ];
  const needsAction = items.filter((i) => i.action);

  return (
    <Paper variant="outlined" sx={{ px: 1.5, py: 1 }} data-testid="mac-status">
      <Stack spacing={1}>
        <Stack direction="row" alignItems="center" spacing={1} useFlexGap flexWrap="wrap">
          <Stack direction="row" alignItems="center" spacing={1} useFlexGap flexWrap="wrap" sx={{ flex: "1 1 0", minWidth: 0 }}>
            <Typography variant="subtitle2" sx={{ mr: 0.5 }}>
              {name || t("mac.status_title")}
            </Typography>
            {items.map((i) => (
              <StatusChip key={i.id} item={i} />
            ))}
            {staged && (
              <Tooltip title={t("mac.memory_help")} arrow>
                <Chip
                  size="small"
                  variant="outlined"
                  label={t("mac.chip_memory", { size: formatBytes(workingSet) })}
                  tabIndex={0}
                  role="img"
                  aria-label={`${t("mac.chip_memory", { size: formatBytes(workingSet) })}. ${t("mac.memory_help")}`}
                  data-testid="mac-memory"
                />
              </Tooltip>
            )}
          </Stack>
          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ flexShrink: 0 }}>
            {action}
            <Tooltip title={open ? t("mac.details_hide") : t("mac.details_show")}>
              <IconButton
                size="small"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
                aria-controls={detailsId}
                aria-label={open ? t("mac.details_hide") : t("mac.details_show")}
                data-testid="mac-details-toggle"
              >
                {open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>
        {needsAction.map((i) => (
          <Alert key={i.id} severity="warning" sx={{ py: 0 }} data-testid={`mac-${i.id}-warning`}>
            {i.text}
          </Alert>
        ))}
        <Collapse in={open} unmountOnExit id={detailsId}>
          <Stack component="ul" spacing={0.5} sx={{ m: 0, pl: 2.5, pb: 0.5 }} data-testid="mac-details">
            {items.map((i) => (
              <Typography key={i.id} component="li" variant="body2" color="text.secondary">
                {i.text}
              </Typography>
            ))}
          </Stack>
        </Collapse>
      </Stack>
    </Paper>
  );
}
