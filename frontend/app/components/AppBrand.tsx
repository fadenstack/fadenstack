import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";
import { APP_BRANDING } from "~/branding";

interface AppBrandProps {
  compact?: boolean;
  /** The line under the name ("Console" by default; the chat says "Chat"). */
  subtitle?: string;
  /** The wordmark's size in px (the sidebar's by default). */
  size?: number;
}

/**
 * The wordmark with its sub-line. Set in the plates' colour
 * (palette.brand.plate): never the accent, never red.
 */
export default function AppBrand({ compact = false, subtitle, size }: AppBrandProps) {
  const { t } = useTranslation();
  const fontSize = size ?? (compact ? 16 : 18);

  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography
        component="div"
        noWrap
        sx={{
          fontSize,
          color: "brand.plate",
          lineHeight: 1.1,
          fontFamily: APP_BRANDING.wordmarkFontFamily,
          fontWeight: APP_BRANDING.wordmarkFontWeight,
          letterSpacing: APP_BRANDING.wordmarkLetterSpacing,
        }}
      >
        {APP_BRANDING.wordmark}
      </Typography>
      <Typography
        variant="caption"
        noWrap
        sx={{
          display: "block",
          color: "text.secondary",
          lineHeight: 1.1,
          mt: 0.25,
          fontFamily: APP_BRANDING.subtitleFontFamily,
        }}
      >
        {subtitle ?? t("app.subtitle")}
      </Typography>
    </Box>
  );
}
