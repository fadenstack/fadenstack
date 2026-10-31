/**
 * PageHelpDrawer — right-side drawer showing contextual help
 * for the current admin page. Triggered by F1 or the "About This Page"
 * menu item.
 */
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Drawer from "@mui/material/Drawer";
import Divider from "@mui/material/Divider";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Typography from "@mui/material/Typography";

import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import ArrowRightIcon from "@mui/icons-material/ArrowRight";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import KeyboardIcon from "@mui/icons-material/Keyboard";
import SchoolIcon from "@mui/icons-material/School";

import { useGuides } from "~/lib/guidesContext";
import { guideTitleKey } from "~/lib/guides";
import { helpUrl } from "~/lib/pageHelp";
import { resolvePageHelp } from "~/lib/pageHelp";

export interface PageHelpDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function PageHelpDrawer({ open, onClose }: PageHelpDrawerProps) {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const { startGuide } = useGuides();

  const entry = resolvePageHelp(location.pathname, location.search);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          sx: { width: 360, maxWidth: "90vw" },
        },
      }}
    >
      <Box
        sx={{
          p: 2.5,
          height: "100%",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Header */}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          <InfoOutlinedIcon color="primary" />
          <Typography variant="h6" sx={{ fontWeight: 600, flex: 1 }}>
            {entry
              ? t(entry.titleKey, { ns: "tour" })
              : t("page_help.unknown.title", { ns: "tour" })}
          </Typography>
        </Box>

        {/* Description */}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {entry
            ? t(entry.descriptionKey, { ns: "tour" })
            : t("page_help.unknown.description", { ns: "tour" })}
        </Typography>

        {/* Actions */}
        {entry && entry.actionKeys.length > 0 && (
          <>
            <Divider sx={{ mb: 1 }} />
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              {t("page_help.actions_heading", { ns: "tour" })}
            </Typography>
            <List dense disablePadding>
              {entry.actionKeys.map((key) => (
                <ListItem key={key} disableGutters sx={{ py: 0.25 }}>
                  <ListItemIcon sx={{ minWidth: 28 }}>
                    <ArrowRightIcon fontSize="small" color="action" />
                  </ListItemIcon>
                  <ListItemText
                    primary={t(key, { ns: "tour" })}
                    primaryTypographyProps={{ variant: "body2" }}
                  />
                </ListItem>
              ))}
            </List>
          </>
        )}

        {entry?.guide && (
          <Box sx={{ mt: 2 }}>
            <Button
              variant="outlined"
              startIcon={<SchoolIcon />}
              onClick={() => {
                onClose();
                startGuide(entry.guide!);
              }}
              data-page-help-guide={entry.guide}
            >
              {t("page_help.show_me", { ns: "tour" })}
            </Button>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5 }}
            >
              {t(guideTitleKey(entry.guide), { ns: "tour" })}
            </Typography>
          </Box>
        )}

        {entry?.doc && (
          <Box sx={{ mt: 2 }}>
            <Button
              variant="text"
              startIcon={<MenuBookOutlinedIcon />}
              href={helpUrl(entry.doc, i18n.language)}
              target="_blank"
              rel="noopener"
              data-page-help-doc={entry.doc}
            >
              {t("page_help.read_more", { ns: "tour" })}
            </Button>
          </Box>
        )}

        {/* Spacer */}
        <Box sx={{ flex: 1 }} />

        {/* Footer */}
        <Divider sx={{ mb: 1.5 }} />
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Chip
            icon={<KeyboardIcon />}
            label="F1"
            size="small"
            variant="outlined"
          />
          <Button variant="text" onClick={onClose}>
            {t("page_help.close", { ns: "tour" })}
          </Button>
        </Box>
      </Box>
    </Drawer>
  );
}
