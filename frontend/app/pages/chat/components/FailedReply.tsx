/**
 * FailedReply — a reply that did not come, and why; tried again from here.
 *
 * For a failure the gateway stored with the chat (it shows after a reload
 * too), and for one only this page saw (the gateway could not be reached).
 */
import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import ReplayIcon from "@mui/icons-material/Replay";
import { useTranslation } from "react-i18next";

interface Props {
  /** Why it failed. */
  message: string;
  /** Tries it again; no button without it (not the chat's last reply). */
  onRetry?: () => void;
  /** The heading, when it says more than "Error" (a rate limit). */
  label?: string;
}

export default function FailedReply({ message, onRetry, label }: Props) {
  const theme = useTheme();
  const { t } = useTranslation();
  return (
    <Box
      data-testid="failed-reply"
      sx={{
        display: "flex",
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 1,
        mb: 1.5,
        px: 1,
      }}
    >
      <Avatar
        sx={{
          width: 32,
          height: 32,
          mt: 0.5,
          bgcolor: theme.palette.error.dark,
          display: { xs: "none", sm: "flex" },
        }}
      >
        <ErrorOutlineIcon sx={{ fontSize: 18 }} />
      </Avatar>
      <Box
        sx={{
          maxWidth: { xs: "100%", sm: "75%" },
          minWidth: 60,
          px: 2,
          py: 1.5,
          borderRadius: "4px 12px 12px 12px",
          bgcolor:
            theme.palette.mode === "dark"
              ? theme.palette.background.paper
              : theme.palette.grey[100],
          border: `1px solid ${theme.palette.error.main}`,
          color: theme.palette.text.primary,
        }}
      >
        <Typography
          variant="caption"
          sx={{ color: "error.main", fontWeight: 600, mb: 0.5, display: "block" }}
        >
          {label ?? t("error_label", { ns: "chat", defaultValue: "Error" })}
        </Typography>
        <Typography variant="body2" sx={{ mb: onRetry ? 1 : 0, wordBreak: "break-word" }}>
          {message}
        </Typography>
        {onRetry && (
          <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
            <Button
              size="small"
              variant="outlined"
              color="error"
              startIcon={<ReplayIcon />}
              onClick={onRetry}
              sx={{ textTransform: "none" }}
            >
              {t("retry", { ns: "chat", defaultValue: "Retry" })}
            </Button>
          </Box>
        )}
      </Box>
    </Box>
  );
}
