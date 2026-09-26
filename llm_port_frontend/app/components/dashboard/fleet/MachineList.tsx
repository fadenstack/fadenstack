/**
 * A group's machines as a list: the accessible, touch-sized way to every
 * machine the cells only draw (spec §6.4.4). A bottom sheet on a phone, a
 * dialog elsewhere. Rows are 48 px, problems first; each opens the machine.
 * For a large fleet the group's machines are fetched here, a page at a time.
 */
import { useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Drawer from "@mui/material/Drawer";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { dashboardFleet, type CapacityGroup, type CapacityMachine } from "~/api/dashboard";
import { fmtAgo, fmtPct } from "../format";
import { HEALTH_LABEL_KEYS, healthSx } from "./paint";

const WORST_FIRST: Record<string, number> = { error: 0, offline: 1, degraded: 2, stale: 3, maintenance: 4, ok: 5 };

interface MachineListProps {
  group: CapacityGroup | null;
  title: string;
  onClose: () => void;
}

export default function MachineList({ group, title, onClose }: MachineListProps) {
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("sm"));
  const content = group ? <Machines group={group} /> : null;

  if (isPhone) {
    return (
      <Drawer
        anchor="bottom"
        open={group !== null}
        onClose={onClose}
        slotProps={{ paper: { sx: { maxHeight: "80vh", borderTopLeftRadius: 16, borderTopRightRadius: 16 } } }}
      >
        <Box sx={{ width: 40, height: 4, borderRadius: 2, bgcolor: "divider", mx: "auto", mt: 1 }} aria-hidden />
        <Typography variant="subtitle1" sx={{ fontWeight: 700, px: 2, pt: 1 }}>
          {title}
        </Typography>
        <Box sx={{ overflow: "auto" }}>{content}</Box>
      </Drawer>
    );
  }
  return (
    <Dialog open={group !== null} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        {content}
      </DialogContent>
    </Dialog>
  );
}

function Machines({ group }: { group: CapacityGroup }) {
  const { t } = useTranslation();
  const [fetched, setFetched] = useState<CapacityMachine[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const inline = group.machines;

  async function loadPage(from?: string) {
    setLoading(true);
    try {
      const page = await dashboardFleet.capacity(group.id ?? "unassigned", from);
      const found = page.groups.find((g) => (g.id ?? "unassigned") === (group.id ?? "unassigned"));
      setFetched((prev) => [...prev, ...(found?.machines ?? [])]);
      setCursor(found?.next_cursor ?? null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (inline === null) void loadPage();
    // Once per group opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.id]);

  const machines = useMemo(
    () => [...(inline ?? fetched)].sort((a, b) => (WORST_FIRST[a.health] ?? 9) - (WORST_FIRST[b.health] ?? 9) || a.name.localeCompare(b.name)),
    [inline, fetched],
  );

  return (
    <>
      <List disablePadding>
        {machines.map((m) => (
          <MachineRow key={m.id} machine={m} />
        ))}
      </List>
      {(loading || cursor) && (
        <Stack alignItems="center" sx={{ py: 1.5 }}>
          {loading ? (
            <CircularProgress size={20} />
          ) : (
            <Button onClick={() => void loadPage(cursor ?? undefined)} sx={{ minHeight: 44 }}>
              {t("dashboard.capacity.load_more")}
            </Button>
          )}
        </Stack>
      )}
    </>
  );
}

function MachineRow({ machine }: { machine: CapacityMachine }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const hottest = machine.gpus.reduce<number | null>((max, g) => (g.temp_c != null && (max == null || g.temp_c > max) ? g.temp_c : max), null);
  return (
    <ListItemButton component={RouterLink} to={`/admin/nodes/${machine.id}`} divider sx={{ minHeight: 48, gap: 1.5 }}>
      <Box aria-hidden sx={{ width: 12, height: 12, borderRadius: "3px", flexShrink: 0, ...healthSx(machine.health, theme) }} />
      <Box sx={{ minWidth: 0, flexGrow: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
          {machine.name}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {t(HEALTH_LABEL_KEYS[machine.health])}
          {machine.health !== "ok" && machine.last_seen ? ` · ${t("dashboard.capacity.seen", { ago: fmtAgo(machine.last_seen, i18n.language) })}` : ""}
        </Typography>
      </Box>
      <Stack alignItems="flex-end" sx={{ flexShrink: 0 }}>
        <Typography variant="caption" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {t("dashboard.capacity.gpu_short", { value: fmtPct(machine.gpu_util_pct, i18n.language, 0) })}
          {" · "}
          {t("dashboard.capacity.vram_short", { value: fmtPct(machine.vram_allocated_pct, i18n.language, 0) })}
        </Typography>
        {hottest != null && (
          <Typography variant="caption" color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {Math.round(hottest)} °C
          </Typography>
        )}
      </Stack>
    </ListItemButton>
  );
}
