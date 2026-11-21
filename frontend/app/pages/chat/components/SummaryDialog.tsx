/**
 * SummaryDialog — the summary a model wrote of a long chat's older messages.
 *
 * EU AI Act Art. 50: it carries a fixed label that says it was written by an AI, here at the text
 * and (``SummaryLabel``) at what opens it; the label's words are fixed by compliance.
 */
import { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import { chatApi } from "~/api/chatClient";
import type { ChatSummary } from "~/api/chatTypes";
import { fmtWhen } from "~/components/usage/usageFormat";

/** The fixed label: a summary is written by an AI and can leave out or get wrong what was said. */
export function SummaryLabel({ dense = false }: { dense?: boolean }) {
  const { t } = useTranslation();
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      dir="auto"
      data-testid="summary-ai-label"
      sx={{ display: "block", ...(dense ? {} : { fontWeight: 600 }) }}
    >
      {t("summary_ai_label", {
        ns: "chat",
        defaultValue: "Summary written by AI. It can leave out or get wrong what was said.",
      })}
    </Typography>
  );
}

interface Props {
  open: boolean;
  sessionId: string;
  onClose: () => void;
}

export default function SummaryDialog({ open, sessionId, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const [summary, setSummary] = useState<ChatSummary | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setState("loading");
    chatApi
      .getSummary(sessionId)
      .then((found) => {
        if (!alive) return;
        setSummary(found);
        setState("ready");
      })
      .catch(() => alive && setState("failed"));
    return () => {
      alive = false;
    };
  }, [open, sessionId]);

  const when = fmtWhen(summary?.created_at, i18n.language);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="summary-dialog-title">
      <DialogTitle id="summary-dialog-title">
        {t("summary_title", { ns: "chat", defaultValue: "Summary of this chat" })}
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        {state === "loading" && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        {state === "failed" && (
          <Typography color="error" variant="body2">
            {t("summary_failed", { ns: "chat", defaultValue: "The summary could not be loaded." })}
          </Typography>
        )}
        {state === "ready" && !summary && (
          <Typography variant="body2">
            {t("summary_none", { ns: "chat", defaultValue: "This chat has no summary." })}
          </Typography>
        )}
        {state === "ready" && summary && (
          <>
            <Box>
              <SummaryLabel />
              {summary.record_id && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  data-testid="summary-marked"
                  sx={{ display: "block" }}
                >
                  {t("summary_marked", { ns: "chat", defaultValue: "Marked as AI-written" })}
                </Typography>
              )}
            </Box>
            <Box
              data-testid="summary-text"
              dir="auto"
              sx={{
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                userSelect: "text",
                maxHeight: "50vh",
                overflowY: "auto",
                p: 1.5,
                borderRadius: 1,
                border: 1,
                borderColor: "divider",
                fontSize: "0.9rem",
              }}
            >
              {summary.summary_text}
            </Box>
            <Typography variant="caption" color="text.secondary" data-testid="summary-meta">
              {[
                summary.model_alias
                  ? t("summary_model", {
                      ns: "chat",
                      defaultValue: "Model: {{model}}",
                      model: summary.model_alias,
                    })
                  : "",
                when ? t("summary_written", { ns: "chat", defaultValue: "Written {{when}}", when }) : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("common.close", { defaultValue: "Close" })}</Button>
      </DialogActions>
    </Dialog>
  );
}
