/**
 * The logo at the start of a phone's top bar. There is no sidebar on a phone,
 * so the logo is where the brand is -- and, like the rail's logo on a
 * desktop, it opens the navigation.
 */
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";

export function NavLogoButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <IconButton
      onClick={onClick}
      aria-label={t("nav.open_navigation", { ns: "common" })}
      sx={{ width: 36, height: 36, p: 0.5, borderRadius: 1, flexShrink: 0 }}
    >
      <Box
        component="img"
        src="/icon_color.png"
        alt=""
        sx={{ width: "100%", height: "100%", objectFit: "contain", borderRadius: 1 }}
      />
    </IconButton>
  );
}
