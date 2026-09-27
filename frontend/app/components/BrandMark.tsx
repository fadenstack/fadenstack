/**
 * The Fadenstack mark, drawn inline so it follows the mode without a request
 * (geometry from brand/README.md, 100 x 100 viewBox).
 *
 * - "iso": three plates in isometric view sharing a front corner -- the logo.
 * - "f": the same plates seen from the front, an F -- for 32 px and less,
 *   where the iso mark's strokes run together.
 *
 * The plates are paper on dark and navy on light; the middle one is the
 * thread, rose-red. Neither ever takes the accent (theme.palette.brand).
 */
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";
import { brandPalette } from "~/theme";

export interface BrandMarkProps {
  /** Rendered width and height in px. */
  size?: number;
  /** "auto": the front-view F up to 32 px, the iso mark above. */
  variant?: "auto" | "iso" | "f";
  /** Announced name; omit when text next to it already says it. */
  title?: string;
}

export default function BrandMark({ size = 40, variant = "auto", title }: BrandMarkProps) {
  const { palette } = useTheme();
  // Outside the app's theme (a bare ThemeProvider) the tokens still hold.
  const { plate, thread } = palette.brand ?? brandPalette(palette.mode);
  const shape = variant === "auto" ? (size <= 32 ? "f" : "iso") : variant;
  const a11y = title ? { role: "img", "aria-label": title } : { "aria-hidden": true };

  return (
    <Box
      component="svg"
      viewBox="0 0 100 100"
      width={size}
      height={size}
      data-brand-mark={shape}
      {...a11y}
      sx={{ display: "block", flexShrink: 0 }}
    >
      {shape === "iso" ? (
        <g transform="translate(0 6)" fill="none" strokeLinejoin="round" strokeLinecap="round" strokeWidth={8}>
          <path d="M50 10L86 28L50 46L14 28Z" stroke={plate} />
          <path d="M14 44L50 62L72.3 50.8" stroke={thread} />
          <path d="M14 60L50 78L57.9 74" stroke={plate} />
        </g>
      ) : (
        <g fill="none" strokeLinecap="round" strokeWidth={12}>
          <path d="M22 26H78" stroke={plate} />
          <path d="M22 50H62" stroke={thread} />
          <path d="M22 74H34" stroke={plate} />
        </g>
      )}
    </Box>
  );
}
