/**
 * MessageBubble — renders a single chat message with markdown, avatar, and token badge.
 */
import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PersonIcon from "@mui/icons-material/Person";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import type { TokenUsage } from "~/api/chatTypes";
import TokenUsageBadge from "./TokenUsageBadge";

interface Props {
  role: "user" | "assistant" | "system";
  content: string;
  modelAlias?: string | null;
  usage?: TokenUsage | null;
  responseMs?: number | null;
  streaming?: boolean;
}

export default function MessageBubble({
  role,
  content,
  modelAlias,
  usage,
  responseMs,
  streaming = false,
}: Props) {
  const theme = useTheme();
  const isUser = role === "user";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: isUser ? "row-reverse" : "row",
        alignItems: "flex-start",
        gap: 1,
        mb: 1.5,
        px: 1,
      }}
    >
      {/* Avatar */}
      <Avatar
        sx={{
          width: 32,
          height: 32,
          mt: 0.5,
          bgcolor: isUser
            ? theme.palette.primary.dark
            : theme.palette.secondary.dark,
        }}
      >
        {isUser ? (
          <PersonIcon sx={{ fontSize: 18 }} />
        ) : (
          <AutoAwesomeIcon sx={{ fontSize: 18 }} />
        )}
      </Avatar>

      <Box
        sx={{
          maxWidth: "75%",
          minWidth: 60,
          px: 2,
          py: 1.5,
          borderRadius: 3,
          bgcolor: isUser
            ? theme.palette.primary.dark
            : theme.palette.background.paper,
          border: isUser ? "none" : `1px solid ${theme.palette.divider}`,
          color: theme.palette.text.primary,
          position: "relative",
        }}
      >
        {/* Model label for assistant */}
        {!isUser && modelAlias && (
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              fontWeight: 600,
              mb: 0.5,
              display: "block",
            }}
          >
            {modelAlias}
          </Typography>
        )}

        {/* Content */}
        <Box
          sx={{
            "& p": { m: 0, mb: 1, "&:last-child": { mb: 0 } },
            "& pre": {
              bgcolor: "rgba(0,0,0,0.3)",
              borderRadius: 1,
              p: 1.5,
              overflowX: "auto",
              fontSize: "0.85rem",
            },
            "& code": {
              fontSize: "0.85rem",
              fontFamily: '"Fira Code", "Fira Mono", monospace',
            },
            "& :not(pre) > code": {
              bgcolor: "rgba(0,0,0,0.2)",
              borderRadius: 0.5,
              px: 0.5,
              py: 0.25,
            },
            "& table": {
              borderCollapse: "collapse",
              width: "100%",
              mb: 1,
            },
            "& th, & td": {
              border: `1px solid ${theme.palette.divider}`,
              px: 1,
              py: 0.5,
              fontSize: "0.85rem",
            },
            "& blockquote": {
              borderLeft: `3px solid ${theme.palette.primary.main}`,
              pl: 1.5,
              ml: 0,
              color: "text.secondary",
            },
            "& a": { color: theme.palette.secondary.main },
            "& ul, & ol": { pl: 2.5, mb: 1 },
            fontSize: "0.925rem",
            lineHeight: 1.65,
            wordBreak: "break-word",
          }}
        >
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {content}
          </ReactMarkdown>

          {/* Blinking cursor for streaming */}
          {streaming && (
            <Box
              component="span"
              sx={{
                display: "inline-block",
                width: 8,
                height: 16,
                bgcolor: "text.primary",
                ml: 0.25,
                animation: "blink 1s step-end infinite",
                "@keyframes blink": {
                  "50%": { opacity: 0 },
                },
              }}
            />
          )}
        </Box>

        {/* Token usage + response time */}
        {(usage || responseMs) && !streaming && (
          <Box
            sx={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 0.5,
              mt: 0.5,
              alignItems: "center",
            }}
          >
            {responseMs != null && (
              <Typography
                variant="caption"
                sx={{ fontSize: "0.7rem", opacity: 0.6 }}
              >
                {responseMs >= 1000
                  ? `${(responseMs / 1000).toFixed(1)}s`
                  : `${responseMs}ms`}
              </Typography>
            )}
            {usage && <TokenUsageBadge usage={usage} />}
          </Box>
        )}
      </Box>
    </Box>
  );
}
