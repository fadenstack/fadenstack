/**
 * Customize on a phone: a full-screen list of every widget with show / hide,
 * move up / down and size. Drag handles are no use on a small touch screen,
 * and the in-place bars would not fit.
 */
import { useTranslation } from "react-i18next";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import type { WidgetSize } from "~/api/dashboard";
import type { GridItem } from "./DashboardGrid";

interface CustomizeDialogProps {
  open: boolean;
  items: GridItem[];
  onClose: () => void;
  onReset: () => void;
  onMove: (id: string, delta: -1 | 1) => void;
  onToggle: (id: string) => void;
  onSize: (id: string, size: WidgetSize) => void;
}

export default function CustomizeDialog({
  open,
  items,
  onClose,
  onReset,
  onMove,
  onToggle,
  onSize,
}: CustomizeDialogProps) {
  const { t } = useTranslation();
  const movable = items.filter((i) => i.available).map((i) => i.def.id);

  return (
    <Dialog fullScreen open={open} onClose={onClose} aria-labelledby="dashboard-customize-title">
      <DialogTitle id="dashboard-customize-title">{t("dashboard.customize")}</DialogTitle>
      <DialogContent dividers sx={{ px: 1 }}>
        <List disablePadding>
          {items.map((item) => {
            const { def, size, hidden, available, reason } = item;
            const title = t(def.titleKey);
            const at = movable.indexOf(def.id);
            return (
              <ListItem key={def.id} divider sx={{ flexDirection: "column", alignItems: "stretch", py: 1, gap: 0.5 }}>
                <Stack direction="row" alignItems="center" gap={0.5}>
                  {/* Switch and title are one label: the whole row is the target. */}
                  <FormControlLabel
                    sx={{ flexGrow: 1, minWidth: 0, m: 0, minHeight: 48 }}
                    disabled={!available}
                    control={
                      <Switch checked={available && !hidden} onChange={() => onToggle(def.id)} />
                    }
                    label={
                      <Stack sx={{ minWidth: 0 }}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {title}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {available ? t(def.descriptionKey) : reason}
                        </Typography>
                      </Stack>
                    }
                  />
                  <IconButton
                    disabled={!available || at <= 0}
                    onClick={() => onMove(def.id, -1)}
                    aria-label={t("dashboard.move_up_named", { name: title })}
                    sx={{ width: 44, height: 44 }}
                  >
                    <ArrowUpwardIcon />
                  </IconButton>
                  <IconButton
                    disabled={!available || at < 0 || at === movable.length - 1}
                    onClick={() => onMove(def.id, 1)}
                    aria-label={t("dashboard.move_down_named", { name: title })}
                    sx={{ width: 44, height: 44 }}
                  >
                    <ArrowDownwardIcon />
                  </IconButton>
                </Stack>
                {available && def.sizes.length > 1 && (
                  <Select
                    size="small"
                    value={size}
                    onChange={(e) => onSize(def.id, e.target.value as WidgetSize)}
                    inputProps={{ "aria-label": t("dashboard.customize_size", { name: title }) }}
                    sx={{ ml: 7, minHeight: 44 }}
                  >
                    {def.sizes.map((s) => (
                      <MenuItem key={s} value={s}>
                        {t(`dashboard.size.${s}`)}
                      </MenuItem>
                    ))}
                  </Select>
                )}
              </ListItem>
            );
          })}
        </List>
      </DialogContent>
      <DialogActions sx={{ justifyContent: "space-between", px: 2 }}>
        <Button onClick={onReset} sx={{ minHeight: 44 }}>
          {t("dashboard.reset_layout")}
        </Button>
        <Button variant="contained" onClick={onClose} sx={{ minHeight: 44 }}>
          {t("dashboard.done")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
