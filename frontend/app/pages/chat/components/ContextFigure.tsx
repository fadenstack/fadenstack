/**
 * ContextFigure — "Context: 12.3k of 25.8k tokens" beside a reply's usage badge.
 *
 * From the turn's ``faden.context``: the context as sent (an estimate) of the budget it was fitted to;
 * the tooltip says what the numbers are and the model's window when it is known.
 */
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import type { ContextUse } from "~/api/chatTypes";

export default function ContextFigure({ context }: { context: ContextUse }) {
  const { t, i18n } = useTranslation();
  const short = new Intl.NumberFormat(i18n.language, { notation: "compact", maximumFractionDigits: 1 });
  const full = new Intl.NumberFormat(i18n.language);
  const detail = [
    t("context_use_tooltip", {
      ns: "chat",
      defaultValue:
        "The context sent to the model for this reply (an estimate), and the budget it was fitted to.",
    }),
    context.window
      ? t("context_use_window", {
          ns: "chat",
          defaultValue: "The model's context window: {{window}} tokens.",
          window: full.format(context.window),
        })
      : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Tooltip title={detail} arrow placement="top">
      <Typography
        variant="caption"
        data-testid="context-figure"
        sx={{ fontSize: "0.7rem", opacity: 0.6 }}
      >
        {t("context_use", {
          ns: "chat",
          defaultValue: "Context: {{used}} of {{budget}} tokens",
          used: short.format(context.tokens),
          budget: short.format(context.budget),
        })}
      </Typography>
    </Tooltip>
  );
}
