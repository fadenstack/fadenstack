/**
 * The owner's note that image generation is off in this version (EU AI Act, Article 5).
 */
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";

export function ImagesOffNote({ sx }: { sx?: React.ComponentProps<typeof Alert>["sx"] }) {
  const { t } = useTranslation();
  return (
    <Alert severity="info" sx={sx} data-testid="images-off-note">
      <AlertTitle>{t("images_off.title")}</AlertTitle>
      {t("images_off.note")}
    </Alert>
  );
}
