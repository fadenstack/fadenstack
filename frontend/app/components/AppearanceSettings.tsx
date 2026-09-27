/**
 * The viewer's accent (Frost / Graphite / Aurora) and mode (dark / light), with a live
 * preview: a choice re-themes the page at once. Both are kept in this
 * browser, as the language is (theme-mode.tsx).
 *
 * The accent colours buttons, links, the current page and charts; the mark
 * and the status colours never follow it (theme.ts).
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import FormControl from "@mui/material/FormControl";
import FormLabel from "@mui/material/FormLabel";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import type { PaletteMode } from "@mui/material";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import LightModeIcon from "@mui/icons-material/LightMode";

import { ACCENT_KEYS, ACCENTS, isAccent, type Accent } from "~/theme";
import { useThemeMode } from "~/theme-mode";

export default function AppearanceSettings() {
  const { t } = useTranslation();
  const { mode, setMode, accent, setAccent } = useThemeMode();

  return (
    <Stack spacing={3}>
      <FormControl component="fieldset">
        <FormLabel component="legend" id="appearance-accent-label" sx={{ fontWeight: 600, color: "text.primary", mb: 0.5 }}>
          {t("settings.appearance.accent")}
        </FormLabel>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          {t("settings.appearance.accent_hint")}
        </Typography>
        <RadioGroup
          row
          aria-labelledby="appearance-accent-label"
          value={accent}
          onChange={(_, value) => isAccent(value) && setAccent(value)}
          sx={{ gap: 1.5 }}
        >
          {ACCENT_KEYS.map((key) => (
            <ChoiceCard
              key={key}
              value={key}
              selected={accent === key}
              label={t(`settings.appearance.accent_${key}`, { defaultValue: ACCENTS[key].label })}
              detail={t(`settings.appearance.accent_${key}_detail`)}
              visual={<AccentSwatch accent={key} mode={mode} />}
            />
          ))}
        </RadioGroup>
      </FormControl>

      <FormControl component="fieldset">
        <FormLabel component="legend" id="appearance-mode-label" sx={{ fontWeight: 600, color: "text.primary", mb: 1.5 }}>
          {t("settings.appearance.mode")}
        </FormLabel>
        <RadioGroup
          row
          aria-labelledby="appearance-mode-label"
          value={mode}
          onChange={(_, value) => (value === "dark" || value === "light") && setMode(value)}
          sx={{ gap: 1.5 }}
        >
          {(["dark", "light"] as const).map((key) => (
            <ChoiceCard
              key={key}
              value={key}
              selected={mode === key}
              label={t(`settings.appearance.mode_${key}`)}
              visual={key === "dark" ? <DarkModeIcon /> : <LightModeIcon />}
            />
          ))}
        </RadioGroup>
      </FormControl>

      <Typography variant="caption" color="text.secondary">
        {t("settings.appearance.saved_here")}
      </Typography>
    </Stack>
  );
}

/** A radio as a card: the whole card is its label, the radio keeps the keyboard. */
function ChoiceCard({
  value,
  selected,
  label,
  detail,
  visual,
}: {
  value: string;
  selected: boolean;
  label: string;
  detail?: string;
  visual: ReactNode;
}) {
  return (
    <Box
      component="label"
      data-choice={value}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.5,
        minWidth: 200,
        minHeight: 48,
        pl: 0.5,
        pr: 2,
        py: 1,
        borderRadius: 1,
        border: 1,
        borderColor: selected ? "primary.main" : "divider",
        boxShadow: (theme) => (selected ? `inset 0 0 0 1px ${theme.palette.primary.main}` : "none"),
        cursor: "pointer",
        transition: "border-color 120ms",
        "&:hover": { borderColor: "primary.main" },
        "&:focus-within": { outline: 2, outlineColor: "primary.main", outlineStyle: "solid", outlineOffset: 2 },
      }}
    >
      <Radio value={value} inputProps={{ "aria-label": label }} />
      <Box sx={{ display: "flex", alignItems: "center", color: "text.secondary" }}>{visual}</Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography fontWeight={600}>{label}</Typography>
        {detail && (
          <Typography variant="caption" color="text.secondary" component="div">
            {detail}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

/** The accent's main colour, with its secondary, as the current mode draws them. */
function AccentSwatch({ accent, mode }: { accent: Accent; mode: PaletteMode }) {
  const def = ACCENTS[accent];
  return (
    <Box aria-hidden sx={{ position: "relative", width: 34, height: 26 }}>
      <Box sx={{ position: "absolute", left: 0, top: 1, width: 24, height: 24, borderRadius: "50%", bgcolor: def.primary[mode].main }} />
      <Box
        sx={{
          position: "absolute",
          left: 16,
          top: 7,
          width: 16,
          height: 16,
          borderRadius: "50%",
          bgcolor: def.secondary[mode].main,
          border: 2,
          borderColor: "background.paper",
        }}
      />
    </Box>
  );
}
