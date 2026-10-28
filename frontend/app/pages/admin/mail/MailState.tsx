/**
 * Whether mail goes out, and if not why; how many mails wait; the last one sent; the last failure. And a
 * failure in words: the mail service's code as a sentence with what to do, and the mail server's own reply.
 */
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";

import { MAIL_FAILURES, type MailState } from "~/api/mailSettings";
import { formatDateTime } from "~/lib/dates";

/** Whether *reason* is a mail server's reply (it starts with an SMTP reply code), not the mail service's words. */
export function isServerReply(reason: string): boolean {
  return /^[2-5]\d\d[\s-]/.test(reason.trim());
}

/**
 * The failure code as a sentence with what to do, and the reason as given: labelled as the mail server's words
 * only when it is its reply; otherwise (the network's or the mail service's own account) as details.
 */
export function FailureText({ code, reason }: { code: string; reason: string }) {
  const { t } = useTranslation();
  const known = (MAIL_FAILURES as readonly string[]).includes(code);
  return (
    <Stack spacing={0.5}>
      <Typography variant="body2" data-testid="mail-failure-text">
        {known ? t(`mail.failure.${code}`) : t("mail.failure.unknown", { code })}
      </Typography>
      {reason && (
        <Box>
          <Typography variant="caption" color="text.secondary" data-testid="mail-failure-reason-label">
            {isServerReply(reason) ? t("mail.failure.server_said") : t("mail.failure.details")}
          </Typography>
          <Box
            component="pre"
            dir="ltr"
            data-testid="mail-failure-reason"
            sx={{
              m: 0,
              mt: 0.5,
              p: 1,
              borderRadius: 1,
              bgcolor: "action.hover",
              fontFamily: "monospace",
              fontSize: "0.8125rem",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              textAlign: "start",
            }}
          >
            {reason}
          </Box>
        </Box>
      )}
    </Stack>
  );
}

function Fact({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography variant="body2" data-testid={testId} sx={{ overflowWrap: "anywhere" }}>
        {value}
      </Typography>
    </Box>
  );
}

export function MailStatePanel({
  state,
  refreshing,
  onRefresh,
}: {
  state: MailState;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const { t } = useTranslation();
  const failure = state.last_failure;
  const failing = state.goes_out && !!failure?.after_last_sent;

  return (
    <Paper sx={{ p: 2 }} data-testid="mail-state">
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Typography variant="subtitle1" component="h2">
          {t("mail.state.title")}
        </Typography>
        <Tooltip title={t("common.refresh")}>
          <span>
            <IconButton size="small" aria-label={t("common.refresh")} disabled={refreshing} onClick={onRefresh}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
      <Divider sx={{ my: 1 }} />
      <Stack spacing={1.5}>
        {!state.goes_out ? (
          <Alert severity="warning" data-testid="mail-goes-out">
            <AlertTitle>{t("mail.state.does_not_go_out")}</AlertTitle>
            <Box component="ul" sx={{ m: 0, paddingInlineStart: 2.5 }}>
              {state.reasons.map((reason) => (
                <li key={reason}>{t(`mail.state.reasons.${reason}`)}</li>
              ))}
            </Box>
          </Alert>
        ) : failing ? (
          <Alert severity="error" data-testid="mail-goes-out">
            {t("mail.state.failing")}
          </Alert>
        ) : (
          <Alert severity="success" data-testid="mail-goes-out">
            {t("mail.state.goes_out")}
          </Alert>
        )}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 1, sm: 4 }} useFlexGap>
          <Fact label={t("mail.state.waiting")} value={String(state.waiting)} testId="mail-waiting" />
          <Fact
            label={t("mail.state.last_sent")}
            value={state.last_sent_at ? formatDateTime(state.last_sent_at) : t("mail.state.never_sent")}
          />
        </Stack>

        {failure && (
          <Box
            data-testid="mail-last-failure"
            sx={{ p: 1.5, border: 1, borderColor: failure.after_last_sent ? "error.main" : "divider", borderRadius: 1 }}
          >
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              {t("mail.state.last_failure")}
            </Typography>
            <Stack spacing={1.5}>
              <FailureText code={failure.code} reason={failure.reason} />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={{ xs: 1, sm: 4 }} useFlexGap flexWrap="wrap">
                <Fact label={t("mail.state.kind")} value={t(`mail.kinds.${failure.kind}`, { defaultValue: failure.kind })} />
                <Fact label={t("mail.state.when")} value={formatDateTime(failure.created_at)} />
                <Fact label={t("mail.state.attempts")} value={String(failure.attempts)} />
                <Fact
                  label={t("mail.state.outcome")}
                  value={
                    failure.gave_up
                      ? t("mail.state.gave_up")
                      : t("mail.state.next_attempt", { when: formatDateTime(failure.next_attempt_at) })
                  }
                />
              </Stack>
              {!failure.after_last_sent && (
                <Typography variant="caption" color="text.secondary">
                  {t("mail.state.sent_since")}
                </Typography>
              )}
            </Stack>
          </Box>
        )}
      </Stack>
    </Paper>
  );
}
