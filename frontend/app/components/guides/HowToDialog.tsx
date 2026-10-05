/**
 * HowToDialog — the how-to guides, from the Help menu. Starting one closes
 * the dialog and walks the task on the real pages.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";

import { useServices } from "~/lib/ServicesContext";
import { availableHowTos, guideDescriptionKey, guideTitleKey } from "~/lib/guides";
import { GuideIcon } from "~/components/guides/GuideIcon";

export interface HowToDialogProps {
  open: boolean;
  isSuperuser: boolean;
  onClose: () => void;
  onStart: (guideId: string) => void;
}

export function HowToDialog({ open, isSuperuser, onClose, onStart }: HowToDialogProps) {
  const { t } = useTranslation("tour");
  const { isModuleEnabled } = useServices();
  const guides = useMemo(() => availableHowTos({ isModuleEnabled, isSuperuser }), [isModuleEnabled, isSuperuser]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="h6" component="span">
          {t("help.how_to_guides")}
        </Typography>
        <IconButton size="small" onClick={onClose} aria-label={t("guides.ui.close")}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("help.how_to_guides_subtitle")}
        </Typography>
        <Stack spacing={1}>
          {guides.map((guide) => (
            <Stack
              key={guide.id}
              direction="row"
              spacing={1.5}
              alignItems="center"
              sx={{ border: 1, borderColor: "divider", borderRadius: 1, px: 1.5, py: 1.25 }}
              data-guide-row={guide.id}
            >
              <GuideIcon name={guide.icon} />
              <Stack sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  {t(guideTitleKey(guide.id))}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t(guideDescriptionKey(guide.id))}
                </Typography>
              </Stack>
              <Button
                size="small"
                variant="outlined"
                onClick={() => {
                  onClose();
                  onStart(guide.id);
                }}
              >
                {t("help.start_guide")}
              </Button>
            </Stack>
          ))}
          {guides.length === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center", py: 2 }}>
              {t("help.no_guides_available")}
            </Typography>
          )}
        </Stack>
      </DialogContent>
    </Dialog>
  );
}
