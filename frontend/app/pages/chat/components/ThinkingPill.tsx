/**
 * ThinkingPill — how much the model thinks before it answers, chosen in the composer.
 *
 * Shows only the levels the chosen model tells apart (Auto always); nothing at all when Auto is
 * the only one. The choice is kept per person and per model (``useChatThinking``).
 */
import { useId, useState } from "react";
import Chip from "@mui/material/Chip";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Tooltip from "@mui/material/Tooltip";
import PsychologyOutlinedIcon from "@mui/icons-material/PsychologyOutlined";
import { useTranslation } from "react-i18next";

import { THINKING_LEVELS, type ThinkingLevel } from "../thinking";

interface Props {
  levels: ThinkingLevel[];
  value: ThinkingLevel;
  onChange: (level: ThinkingLevel) => void;
}

export default function ThinkingPill({ levels, value, onChange }: Props) {
  const { t } = useTranslation();
  const menuId = useId();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const name = (level: ThinkingLevel) => t(`thinking_${level}`, { ns: "chat" });

  return (
    <>
      <Tooltip
        title={t("thinking_tooltip", {
          ns: "chat",
          defaultValue: "How much the model thinks before it answers. Kept for each model.",
        })}
      >
        <Chip
          icon={<PsychologyOutlinedIcon sx={{ fontSize: 16 }} />}
          label={t("thinking_pill", { ns: "chat", defaultValue: "Thinking: {{level}}", level: name(value) })}
          size="small"
          variant="outlined"
          clickable
          onClick={(e) => setAnchor(e.currentTarget)}
          aria-haspopup="menu"
          aria-controls={anchor ? menuId : undefined}
          aria-expanded={anchor ? true : undefined}
          aria-label={t("thinking_aria", {
            ns: "chat",
            defaultValue: "Thinking level: {{level}}",
            level: name(value),
          })}
          data-testid="thinking-pill"
          sx={{ height: 32, fontSize: "0.8rem", maxWidth: "100%" }}
        />
      </Tooltip>
      <Menu
        id={menuId}
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        {THINKING_LEVELS.filter((level) => levels.includes(level)).map((level) => (
          <MenuItem
            key={level}
            dense
            selected={level === value}
            sx={{ minHeight: 40 }}
            onClick={() => {
              setAnchor(null);
              onChange(level);
            }}
          >
            <ListItemText primary={name(level)} />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
