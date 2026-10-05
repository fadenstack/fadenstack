/**
 * GettingStarted — the way from a fresh install to people using it, as a
 * list that ticks itself from what the server has. On the dashboard until
 * hidden; from the Help menu at any time.
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Collapse from "@mui/material/Collapse";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CloseIcon from "@mui/icons-material/Close";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";

import { useServices } from "~/lib/ServicesContext";
import { useGuides } from "~/lib/guidesContext";
import {
  useGettingStarted,
  visibleItems,
  type ChecklistItem,
  type ChecklistState,
} from "~/lib/gettingStarted";

function ItemRow({
  item,
  done,
  onShowMe,
  onOpen,
  number,
}: {
  item: ChecklistItem;
  done: boolean | undefined;
  onShowMe?: () => void;
  onOpen: () => void;
  number?: number;
}) {
  const { t } = useTranslation("tour");
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 1, sm: 1.5 }}
      alignItems={{ sm: "center" }}
      sx={{ py: 1.25, borderTop: 1, borderColor: "divider" }}
      data-checklist-item={item.id}
      data-done={done ? "true" : "false"}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start" sx={{ flexGrow: 1, minWidth: 0 }}>
        {done ? (
          <CheckCircleIcon color="success" sx={{ mt: 0.25 }} />
        ) : (
          <RadioButtonUncheckedIcon color="disabled" sx={{ mt: 0.25 }} />
        )}
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, textDecoration: done ? "none" : undefined }}>
            {number ? `${number}. ` : ""}
            {t(`checklist.${item.id}.title`)}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t(`checklist.${item.id}.body`)}
          </Typography>
        </Box>
      </Stack>
      <Stack direction="row" spacing={1} sx={{ flexShrink: 0, pl: { xs: 4.5, sm: 0 } }}>
        {onShowMe && !done && (
          <Button size="small" variant="outlined" onClick={onShowMe}>
            {t("checklist.show_me")}
          </Button>
        )}
        <Button size="small" variant={done ? "text" : "contained"} onClick={onOpen}>
          {t(item.id === "chat" ? "checklist.open_chat" : "checklist.open")}
        </Button>
      </Stack>
    </Stack>
  );
}

function ChecklistBody({
  state,
  onShowMe,
  onOpen,
}: {
  state: ChecklistState | null;
  onShowMe: (guide: string) => void;
  onOpen: (item: ChecklistItem) => void;
}) {
  const { t } = useTranslation("tour");
  const { isModuleEnabled } = useServices();
  const items = useMemo(() => visibleItems(isModuleEnabled), [isModuleEnabled]);
  const main = items.filter((i) => i.main);
  const more = items.filter((i) => !i.main);
  const [showMore, setShowMore] = useState(false);

  return (
    <>
      <Box>
        {main.map((item, n) => (
          <ItemRow
            key={item.id}
            item={item}
            number={n + 1}
            done={state?.[item.id]}
            onShowMe={item.guide ? () => onShowMe(item.guide!) : undefined}
            onOpen={() => onOpen(item)}
          />
        ))}
      </Box>
      <Button
        size="small"
        onClick={() => setShowMore((v) => !v)}
        endIcon={showMore ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        sx={{ mt: 1 }}
      >
        {t("checklist.more")}
      </Button>
      <Collapse in={showMore}>
        <Box>
          {more.map((item) => (
            <ItemRow
              key={item.id}
              item={item}
              done={state?.[item.id]}
              onShowMe={item.guide ? () => onShowMe(item.guide!) : undefined}
              onOpen={() => onOpen(item)}
            />
          ))}
        </Box>
      </Collapse>
    </>
  );
}

function useChecklistActions(savePrefs: (p: { chat_opened?: boolean }) => Promise<void>, after?: () => void) {
  const navigate = useNavigate();
  const { startGuide } = useGuides();
  return {
    onShowMe: (guide: string) => {
      after?.();
      startGuide(guide);
    },
    onOpen: (item: ChecklistItem) => {
      after?.();
      if (item.id === "chat") void savePrefs({ chat_opened: true });
      if (item.id === "tour") startGuide("console_tour");
      else navigate(item.route);
    },
  };
}

/** On the dashboard, until hidden. Shows nothing for a list that is not loaded yet or was hidden. */
export function GettingStartedCard() {
  const { t } = useTranslation("tour");
  const { isModuleEnabled } = useServices();
  const { state, prefs, savePrefs } = useGettingStarted(isModuleEnabled, true);
  const actions = useChecklistActions(savePrefs);

  if (!state || prefs.dismissed) return null;
  const main = visibleItems(isModuleEnabled).filter((i) => i.main);
  const done = main.filter((i) => state[i.id]).length;
  const allDone = done === main.length;

  return (
    <Card variant="outlined" data-tour-id="dashboard.getting-started">
      <CardContent sx={{ pb: "12px !important" }}>
        <Stack direction="row" alignItems="flex-start" spacing={2}>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              {allDone ? t("checklist.done_title") : t("checklist.title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {allDone ? t("checklist.done_body") : t("checklist.subtitle")}
            </Typography>
          </Box>
          <Button size="small" color="inherit" onClick={() => void savePrefs({ dismissed: true })} data-checklist-hide="">
            {t("checklist.hide")}
          </Button>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ my: 1.5 }}>
          <LinearProgress
            variant="determinate"
            value={(done / main.length) * 100}
            color={allDone ? "success" : "primary"}
            sx={{ flexGrow: 1, height: 6, borderRadius: 1 }}
          />
          <Typography variant="caption" color="text.secondary">
            {t("checklist.progress", { done, total: main.length })}
          </Typography>
        </Stack>
        <ChecklistBody state={state} {...actions} />
      </CardContent>
    </Card>
  );
}

/** From the Help menu: the same list, also after it was hidden from the dashboard. */
export function GettingStartedDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation("tour");
  const { isModuleEnabled } = useServices();
  const { state, prefs, savePrefs } = useGettingStarted(isModuleEnabled, open);
  const actions = useChecklistActions(savePrefs, onClose);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogContent>
        <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ mb: 1 }}>
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              {t("checklist.title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("checklist.subtitle")}
            </Typography>
          </Box>
          {prefs.dismissed && (
            <Button size="small" onClick={() => void savePrefs({ dismissed: false })}>
              {t("checklist.show_on_dashboard")}
            </Button>
          )}
          <IconButton size="small" onClick={onClose} aria-label={t("guides.ui.close")}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
        {state ? <ChecklistBody state={state} {...actions} /> : <LinearProgress sx={{ my: 3 }} />}
      </DialogContent>
    </Dialog>
  );
}
