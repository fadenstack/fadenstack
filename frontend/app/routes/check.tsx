/**
 * The public check page (/check), outside the signed-in layouts like the
 * sign-in page: anyone checks a picture or a file for this installation's
 * marks of AI-generated content, without an account, when an administrator
 * offers the page (Settings > Modules > Provenance). Texts are checked for
 * people with an account; the page names whom to ask.
 *
 * Not offered, it says so and nothing else. Nothing submitted is kept.
 */
import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { checkContentPublicly, fetchPublicCheck, type PublicCheckInfo } from "~/api/provenance";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";
import ContentCheck from "~/components/contentCheck/ContentCheck";

type PageState = { kind: "loading" } | { kind: "offered"; info: PublicCheckInfo } | { kind: "not_offered" };

export default function PublicCheckPage() {
  const { t } = useTranslation();
  const [page, setPage] = useState<PageState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchPublicCheck()
      .then((info) => {
        if (!cancelled) setPage({ kind: "offered", info });
      })
      .catch(() => {
        // 404 when it is not offered; any other failure leaves nothing to offer either.
        if (!cancelled) setPage({ kind: "not_offered" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  let body: React.ReactNode;
  if (page.kind === "loading") {
    body = <CircularProgress aria-label={t("common.loading")} />;
  } else if (page.kind === "not_offered") {
    body = (
      <Alert severity="info" data-testid="public-check-not-offered">
        <Typography variant="body2" fontWeight={600}>
          {t("content_check.public.not_offered")}
        </Typography>
        <Typography variant="body2">{t("content_check.public.not_offered_hint")}</Typography>
      </Alert>
    );
  } else {
    const { contact, max_bytes } = page.info;
    body = (
      <ContentCheck
        mode="public"
        submit={checkContentPublicly}
        maxBytes={max_bytes}
        textChecks={
          <Alert severity="info" data-testid="public-check-texts">
            <Typography variant="body2">{t("content_check.public.text_checks")}</Typography>
            <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
              {contact ? t("content_check.public.contact", { contact }) : t("content_check.public.no_contact")}
            </Typography>
          </Alert>
        }
      />
    );
  }

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        justifyContent: "center",
        alignItems: { xs: "flex-start", sm: "center" },
        p: 2,
      }}
    >
      <Card sx={{ width: "100%", maxWidth: 760 }}>
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          <Stack spacing={2.5}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <BrandMark size={40} />
              <AppBrand size={22} />
            </Stack>
            <Box>
              <Typography variant="h5" component="h1" fontWeight={700}>
                {t("content_check.public.title")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t("content_check.public.intro")}
              </Typography>
            </Box>
            {body}
            <Button component={RouterLink} to="/login" size="small" sx={{ alignSelf: "flex-start" }}>
              {t("content_check.public.sign_in")}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
