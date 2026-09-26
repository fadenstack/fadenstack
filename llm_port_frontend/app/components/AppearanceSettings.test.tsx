import { useMemo, useState } from "react";
import type { PaletteMode } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AppearanceSettings from "./AppearanceSettings";
import BrandMark from "./BrandMark";
import { getAppTheme, type Accent } from "~/theme";
import { ThemeModeContext } from "~/theme-mode";

/** The app's provider in small: the choices re-theme what is rendered inside. */
function Themed({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<PaletteMode>("dark");
  const [accent, setAccent] = useState<Accent>("nord");
  const value = useMemo(
    () => ({ mode, setMode, toggleMode: () => setMode((m) => (m === "dark" ? "light" : "dark")), accent, setAccent }),
    [mode, accent],
  );
  return (
    <ThemeModeContext.Provider value={value}>
      <ThemeProvider theme={getAppTheme(mode, accent)}>{children}</ThemeProvider>
    </ThemeModeContext.Provider>
  );
}

describe("the appearance settings", () => {
  it("offer Nord (chosen) and Aurora as radios with their names", () => {
    render(<AppearanceSettings />, { wrapper: Themed });
    const accent = screen.getByRole("radiogroup", { name: "Accent" });
    expect(accent).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Nord" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Aurora" })).not.toBeChecked();
  });

  it("switch the accent and the mode at once", () => {
    render(<AppearanceSettings />, { wrapper: Themed });
    fireEvent.click(screen.getByRole("radio", { name: "Aurora" }));
    expect(screen.getByRole("radio", { name: "Aurora" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Light" }));
    expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
  });
});

describe("the mark", () => {
  it("is the front-view F up to 32 px and the iso mark above", () => {
    const { container } = render(
      <>
        <BrandMark size={28} />
        <BrandMark size={40} title="Fadenstack" />
      </>,
      { wrapper: Themed },
    );
    const marks = container.querySelectorAll("svg[data-brand-mark]");
    expect(marks[0].getAttribute("data-brand-mark")).toBe("f");
    expect(marks[1].getAttribute("data-brand-mark")).toBe("iso");
    expect(screen.getByRole("img", { name: "Fadenstack" })).toBe(marks[1]);
  });

  it("keeps its colours whatever the accent", () => {
    const strokes = (accent: Accent) => {
      const { container, unmount } = render(
        <ThemeProvider theme={getAppTheme("dark", accent)}>
          <BrandMark size={40} />
        </ThemeProvider>,
      );
      const colours = [...container.querySelectorAll("path")].map((p) => p.getAttribute("stroke"));
      unmount();
      return colours;
    };
    expect(strokes("nord")).toEqual(["#F3F4F6", "#FF4D6D", "#F3F4F6"]);
    expect(strokes("aurora")).toEqual(strokes("nord"));
  });
});
