/**
 * OlderMessagesNote — the quiet line, once per chat, where older messages stop going to the model.
 *
 * With a summary written by a model standing for them, it says so (and offers it, see SummaryDialog).
 */
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import { SummaryLabel } from "./SummaryDialog";

export default function OlderMessagesNote({
  summary,
  onShowSummary,
}: {
  summary: boolean;
  /** Opens the summary (the dialog); the action shows only with a summary. */
  onShowSummary?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Box data-testid="older-messages-note" role="note" sx={{ px: 2, py: 0.5, mb: 1.5, textAlign: "center" }}>
      <Typography variant="caption" color="text.secondary" dir="auto">
        {summary
          ? t("older_summarised", {
              ns: "chat",
              defaultValue:
                "Older messages are no longer sent to the model; a summary written by the model stands for them.",
            })
          : t("older_dropped", {
              ns: "chat",
              defaultValue:
                "Older messages are no longer sent to the model. A model that uses tools can still search them.",
            })}
      </Typography>
      {summary && onShowSummary && (
        <Box sx={{ mt: 0.5 }}>
          <Button size="small" onClick={onShowSummary} sx={{ textTransform: "none" }}>
            {t("summary_show", { ns: "chat", defaultValue: "Show summary" })}
          </Button>
          <SummaryLabel dense />
        </Box>
      )}
    </Box>
  );
}
