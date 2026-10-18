/**
 * Check content (/check-content): for everyone signed in with
 * `provenance:check`, beside the chat. Paste a text or choose a picture or a
 * file and read whether it carries this installation's marks of AI-generated
 * content; the result can be downloaded signed.
 *
 * Shown only while the provenance module is on, like the user's skills page.
 */
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import { checkContent } from "~/api/provenance";
import { UserPageFrame } from "~/components/UserPageFrame";
import ContentCheck from "~/components/contentCheck/ContentCheck";
import { useContentCheck } from "~/lib/useContentCheck";

export default function CheckContentPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state, maxBytes } = useContentCheck();

  let body: React.ReactNode;
  if (state === "loading") {
    body = <CircularProgress aria-label={t("common.loading")} />;
  } else if (state === "denied") {
    body = <Alert severity="warning">{t("content_check.no_access")}</Alert>;
  } else if (state === "off") {
    body = <Alert severity="info">{t("content_check.module_off")}</Alert>;
  } else {
    body = <ContentCheck mode="account" submit={checkContent} maxBytes={maxBytes} />;
  }

  return (
    <UserPageFrame maxWidth={900}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1 }}>
        <IconButton onClick={() => navigate("/chat")} aria-label={t("content_check.back_to_chat")}>
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h5" component="h1">
          {t("content_check.title")}
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        {t("content_check.intro")}
      </Typography>
      {body}
    </UserPageFrame>
  );
}
