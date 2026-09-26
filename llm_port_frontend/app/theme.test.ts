import { getContrastRatio } from "@mui/material/styles";
import type { PaletteMode } from "@mui/material";
import { describe, expect, it } from "vitest";

import { ACCENT_KEYS, ACCENTS, DEFAULT_ACCENT, getAppTheme, isAccent, type Accent } from "./theme";

const MODES: PaletteMode[] = ["dark", "light"];
const CASES = ACCENT_KEYS.flatMap((accent) => MODES.map((mode) => [accent, mode] as [Accent, PaletteMode]));

describe("the accent", () => {
  it("is Nord unless another is chosen", () => {
    expect(DEFAULT_ACCENT).toBe("nord");
    expect(getAppTheme("dark").palette.primary.main).toBe("#22C1D3");
    expect(isAccent("aurora")).toBe(true);
    expect(isAccent("blue")).toBe(false);
  });

  it.each([
    ["nord", "dark", "#22C1D3", "#06202A"],
    ["nord", "light", "#0E7C86", "#FFFFFF"],
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
    expect(palette.accent.navActiveBg).toBe(tokens.primary[mode].dark);
  });
});

describe("what the accent never changes", () => {
  it.each(MODES)("status, health and backgrounds are the same for every accent (%s)", (mode) => {
    const [a, b] = ACCENT_KEYS.map((accent) => getAppTheme(mode, accent).palette);
    for (const role of ["error", "warning", "success"] as const) {
      expect(a[role].main).toBe(b[role].main);
    }
    expect(a.error.main).toBe("#e53935");
    expect(a.warning.main).toBe("#fb8c00");
    expect(a.success.main).toBe("#2e7d32");
    expect(a.dashboard.health).toEqual(b.dashboard.health);
    expect(a.background).toEqual(b.background);
  });

  it.each(MODES)("the mark keeps its plates and thread (%s)", (mode) => {
    const [a, b] = ACCENT_KEYS.map((accent) => getAppTheme(mode, accent).palette.brand);
    expect(a).toEqual(b);
    expect(a).toEqual(mode === "dark" ? { plate: "#F3F4F6", thread: "#FF4D6D" } : { plate: "#0A0E1A", thread: "#E2325A" });
  });
});

describe("contrast", () => {
  it.each(CASES)("%s in %s mode: text on the accent 4.5:1, the accent on paper 3:1", (accent, mode) => {
    const { palette } = getAppTheme(mode, accent);
    const paper = palette.background.paper;
    expect(getContrastRatio(palette.primary.contrastText, palette.primary.main)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.accent.navActiveText, palette.accent.navActiveBg)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.accent.text, paper)).toBeGreaterThanOrEqual(4.5);
    expect(getContrastRatio(palette.primary.main, paper)).toBeGreaterThanOrEqual(3);
    expect(getContrastRatio(palette.secondary.main, paper)).toBeGreaterThanOrEqual(3);
    for (const colour of Object.values(palette.dashboard.residency)) {
      expect(getContrastRatio(colour, paper)).toBeGreaterThanOrEqual(3);
    }
  });
});
