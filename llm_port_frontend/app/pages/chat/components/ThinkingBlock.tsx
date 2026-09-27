/**
 * What a reasoning model thought before it answered.
 *
 * While it thinks (``live``) the last lines scroll by under "Thinking…", so a
 * model that reasons for seconds does not look stuck -- with OpenRouter's
 * reasoning models the chat showed nothing for 1-14 s. Once it answers, the
 * thinking folds into "Thought for N s", to open on demand.
 *
 * Kept in the page only: the thinking is not saved with the reply.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Typography from "@mui/material/Typography";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PsychologyOutlinedIcon from "@mui/icons-material/PsychologyOutlined";

interface ThinkingBlockProps {
  text: string;
  /** Still thinking: the answer has not begun. */
  live?: boolean;
  /** How long it thought, once it answered. */
  ms?: number | null;
}

const textSx = {
  color: "text.secondary",
  fontSize: "0.8rem",
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

export default function ThinkingBlock({ text, live = false, ms = null }: ThinkingBlockProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  if (live) {
    return (
      <Box data-testid="thinking" aria-live="polite">
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
          <CircularProgress size={12} />
          <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary" }}>
            {t("thinking", { ns: "chat", defaultValue: "Thinking…" })}
          </Typography>
        </Box>
        {/* The last few lines: newest at the bottom, the rest faded away above. */}
        <Box
          sx={{
            ...textSx,
            maxHeight: "4.5em",
            overflow: "hidden",
            display: "flex",
            flexDirection: "column-reverse",
            maskImage: "linear-gradient(to bottom, transparent, black 40%)",
          }}
        >
          <span>{text}</span>
        </Box>
      </Box>
    );
  }

  const seconds = ms !== null ? Math.max(1, Math.round(ms / 1000)) : null;
  return (
    <Box data-testid="thought" sx={{ mb: 1 }}>
      <ButtonBase
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        sx={{ gap: 0.5, borderRadius: 1, px: 0.5, py: 0.25, ml: -0.5, color: "text.secondary" }}
      >
        <PsychologyOutlinedIcon sx={{ fontSize: 16 }} />
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          {seconds !== null
            ? t("thought_for", { ns: "chat", seconds, defaultValue: "Thought for {{seconds}} s" })
            : t("thought", { ns: "chat", defaultValue: "Thought" })}
        </Typography>
        <ExpandMoreIcon
          sx={{ fontSize: 16, transition: "transform 0.2s", transform: open ? "rotate(180deg)" : "none" }}
        />
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ ...textSx, borderLeft: 2, borderColor: "divider", pl: 1.5, mt: 0.5 }}>{text}</Box>
      </Collapse>
    </Box>
  );
}
