import type { Route } from "./+types/home";
import { Link as RouterLink } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import { useTranslation } from "react-i18next";
import AppBrand from "~/components/AppBrand";
import BrandMark from "~/components/BrandMark";
import { APP_BRANDING } from "~/branding";

export function meta({}: Route.MetaArgs) {
  return [
    { title: APP_BRANDING.name },
    { name: "description", content: "The self-hosted LLM control plane" },
  ];
}

export default function Home() {
  const { t } = useTranslation();
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        bgcolor: "background.default",
      }}
    >
      <Stack alignItems="center" spacing={3} sx={{ p: 4 }}>
        <Stack direction="row" alignItems="center" spacing={2}>
          <BrandMark size={88} title={APP_BRANDING.name} />
          <AppBrand size={44} subtitle={t("app.subtitle")} />
        </Stack>
        <Typography
          variant="body1"
          color="text.secondary"
          textAlign="center"
          maxWidth={420}
        >
          {t("home.subtitle")}
        </Typography>
        <Button
          component={RouterLink}
          to="/login"
          variant="contained"
          size="large"
          sx={{ px: 4, py: 1.2 }}
        >
          {t("auth.sign_in")}
        </Button>
      </Stack>
    </Box>
  );
}
