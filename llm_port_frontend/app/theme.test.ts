import { getContrastRatio } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";
import { describe, expect, it } from "vitest";

import { ACCENT_KEYS, ACCENTS, DEFAULT_ACCENT, getAppTheme, isAccent, type Accent } from "./theme";

const MODES: PaletteMode[] = ["dark", "light"];
const CASES = ACCENT_KEYS.flatMap((accent) => MODES.map((mode) => [accent, mode] as [Accent, PaletteMode]));

describe("the accent", () => {
  it("is Frost unless another is chosen, and Nord is no longer one", () => {
    expect(DEFAULT_ACCENT).toBe("frost");
    expect(ACCENT_KEYS).toEqual(["frost", "graphite", "aurora"]);
    expect(getAppTheme("dark").palette.primary.main).toBe("#81A1C1");
    expect(isAccent("graphite")).toBe(true);
    expect(isAccent("nord")).toBe(false);
    expect(isAccent("blue")).toBe(false);
  });

  it.each([
    ["frost", "dark", "#81A1C1", "#0A0E1A"],
    ["frost", "light", "#4C6A92", "#FFFFFF"],
    ["graphite", "dark", "#E5E7EB", "#111827"],
    ["graphite", "light", "#1F2937", "#FFFFFF"],
    ["aurora", "dark", "#7C4DFF", "#FFFFFF"],
    ["aurora", "light", "#7C4DFF", "#FFFFFF"],
  ] as const)("%s in %s mode fills with %s and writes %s on it", (accent, mode, main, text) => {
    const { primary } = getAppTheme(mode, accent).palette;
    expect(primary.main).toBe(main);
    expect(primary.contrastText).toBe(text);
  });

  it.each(CASES)("%s in %s mode takes its colours from the tokens", (accent, mode) => {
    const { palette } = getAppTheme(mode, accent);
    const tokens = ACCENTS[accent];
    expect(palette.primary).toMatchObject(tokens.primary[mode]);
    expect(palette.secondary).toMatchObject(tokens.secondary[mode]);
    expect(palette.dashboard.residency).toEqual(tokens.residency[mode]);
    expect(palette.accent.navActiveBg).toBe(tokens.nav[mode].bg);
    expect(palette.accent.text).toBe(tokens.text[mode]);
  });
});

describe("what the accent never changes", () => {
  it.each(MODES)("status, health and backgrounds are the same for every accent (%s)", (mode) => {
    const [a, ...others] = ACCENT_KEYS.map((accent) => getAppTheme(mode, accent).palette);
    for (const b of others) {
      for (const role of ["error", "warning", "success"] as const) {
        expect(b[role].main).toBe(a[role].main);
      }
      expect(b.dashboard.health).toEqual(a.dashboard.health);
      expect(b.background).toEqual(a.background);
    }
    expect(a.error.main).toBe("#e53935");
    expect(a.warning.main).toBe("#fb8c00");
    expect(a.success.main).toBe("#2e7d32");
  });

  it.each(MODES)("the mark keeps its plates and thread (%s)", (mode) => {
    const [a, ...others] = ACCENT_KEYS.map((accent) => getAppTheme(mode, accent).palette.brand);
    for (const b of others) expect(b).toEqual(a);
    expect(a).toEqual(mode === "dark" ? { plate: "#F3F4F6", thread: "#FF4D6D" } : { plate: "#0A0E1A", thread: "#E2325A" });
  });
});

describe("contrast", () => {
  it.each(CASES)("%s in %s mode: text on the accent 4.5:1, hovered too, the accent on paper 3:1", (accent, mode) => {
    const { palette } = getAppTheme(mode, accent);
    const paper = palette.background.paper;
    expect(getContrastRatio(palette.primary.contrastText, palette.primary.main)).toBeGreaterThanOrEqual(4.5);
    // A contained button is filled with primary.dark on hover, its text unchanged.
    expect(getContrastRatio(palette.primary.contrastText, palette.primary.dark)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.accent.navActiveText, palette.accent.navActiveBg)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.accent.text, paper)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.primary.main, paper)).toBeGreaterThanOrEqual(3);
    expect(getContrastRatio(palette.secondary.main, paper)).toBeGreaterThanOrEqual(3);
    for (const colour of Object.values(palette.dashboard.residency)) {
      expect(getContrastRatio(colour, paper)).toBeGreaterThanOrEqual(3);
    }
  });
});
