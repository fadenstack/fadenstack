/**
 * GuideRunner: the overlay's cut-out follows a target the page moves.
 *
 * On Clusters, the card "Running on your machines, not managed here" loads after the guide "Create a cluster" has
 * opened and pushes the button down. Joyride redrew its cut-out only on a scroll, a resize or a change of the
 * target's size, so the cut-out stayed where the button had been and the overlay took the click on the button.
 */
import { render, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { getAppTheme } from "~/theme";
import { GuideRunner } from "./GuideRunner";

const TARGET = "[data-tour-id='clusters.create']";

function rect(top: number, left: number, width: number, height: number): DOMRect {
  return { top, left, width, height, bottom: top + height, right: left + width, x: left, y: top, toJSON: () => ({}) };
}

/** The overlay's path: the whole window, then the cut-out ("M<left> <top>H..."). */
function cutout(): string {
  return document.querySelector("[data-testid='overlay'] path")?.getAttribute("d") ?? "";
}

describe("GuideRunner", () => {
  it("moves the overlay's cut-out with a target the page moves", async () => {
    let top = 163;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.matches(TARGET) ? rect(top, 600, 150, 37) : rect(0, 0, 0, 0);
    });

    render(
      <ThemeProvider theme={getAppTheme("light")}>
        <MemoryRouter initialEntries={["/admin/clusters"]}>
          <button type="button" data-tour-id="clusters.create">
            Create a cluster
          </button>
          <GuideRunner guideId="create_cluster" isSuperuser onEnd={() => {}} />
        </MemoryRouter>
      </ThemeProvider>,
    );

    // The step shows, with the cut-out around the button: 6 px of padding, corners of radius 4 (the path starts
    // after the top-left corner).
    await waitFor(() => expect(document.querySelector("[data-guide-tooltip]")).not.toBeNull());
    await waitFor(() => expect(cutout()).toContain(" M598 157H"));

    // A card loads above: the button moves down, its size the same.
    top = 399;
    await waitFor(() => expect(cutout()).toContain(" M598 393H"));
  });
});
