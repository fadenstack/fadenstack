/**
 * The logo at the start of a phone's top bar. There is no sidebar on a phone,
 * so the logo is where the brand is -- and, like the rail's logo on a
 * desktop, it opens the navigation.
 */
import { useTranslation } from "react-i18next";
import IconButton from "@mui/material/IconButton";
import BrandMark from "~/components/BrandMark";

export function NavLogoButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <IconButton
      onClick={onClick}
      aria-label={t("nav.open_navigation", { ns: "common" })}
      sx={{ width: 40, height: 40, p: 0, borderRadius: 1, flexShrink: 0 }}
    >
      <BrandMark size={36} />
    </IconButton>
  );
}
