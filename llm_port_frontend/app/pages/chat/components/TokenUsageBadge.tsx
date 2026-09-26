/**
 * TokenUsageBadge — compact chip showing token counts for a message.
 *
 * With the request's usage it shows the total (prompt + reply), live and
 * after a reload alike -- the stored reply brings the usage its request
 * logged. Without it (no logged request) it shows only the reply's own size
 * and says so, rather than a made-up "Prompt: 0".
 */
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import { useTranslation } from "react-i18next";

import type { TokenUsage } from "~/api/chatTypes";

type Props = { usage: TokenUsage; replyTokens?: never } | { replyTokens: number; usage?: never };

function fmtNum(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export default function TokenUsageBadge({ usage, replyTokens }: Props) {
  const { t } = useTranslation();
  const count = usage ? usage.total_tokens : replyTokens;
  const label = `${fmtNum(count)} ${t("token_suffix", {
    ns: "chat",
    defaultValue: "tok",
  })}`;
  const detail = usage
    ? t("chat.token_detail", {
        defaultValue:
          "Prompt: {{prompt}} · Completion: {{completion}} · Total: {{total}}",
        prompt: usage.prompt_tokens.toLocaleString(),
        completion: usage.completion_tokens.toLocaleString(),
        total: usage.total_tokens.toLocaleString(),
      })
    : t("chat.token_reply_only", {
        defaultValue: "Reply: {{completion}} tokens",
        completion: count.toLocaleString(),
      });

  return (
    <Tooltip title={detail} arrow>
      <Chip
        label={label}
        size="small"
        variant="outlined"
        sx={{ fontSize: "0.7rem", height: 20, opacity: 0.7 }}
      />
    </Tooltip>
  );
}
