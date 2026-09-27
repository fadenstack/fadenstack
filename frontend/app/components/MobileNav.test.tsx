import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import type { NavEntry } from "~/lib/adminConstants";
import { NAV_HAND_KEY, useNavHand } from "~/lib/useNavHand";
import { ADMIN_NAV_ID, AdminSidebar } from "./AdminSidebar";
import { MobileNavButton } from "./MobileNavButton";

const ENTRIES: NavEntry[] = [
  { id: "dashboard", kind: "leaf", to: "/admin/dashboard", labelKey: "nav.dashboard", icon: <span /> },
  {
    id: "llm",
    kind: "group",
    labelKey: "nav.llm_group",
    icon: <span />,
    children: [{ to: "/admin/llm/providers", labelKey: "nav.providers", icon: <span /> }],
  },
];

/** The admin layout's phone wiring, without its auth and data. */
function PhoneShell() {
  const [open, setOpen] = useState(false);
  const [hand, setHand] = useNavHand();
  return (
    <MemoryRouter initialEntries={["/admin/dashboard"]}>
      <MobileNavButton hand={hand} open={open} onOpen={() => setOpen(true)} controls={ADMIN_NAV_ID} />
      <AdminSidebar
        drawerOpen={false}
        setDrawerOpen={() => {}}
        mainVisible={ENTRIES}
        pinnedVisible={[]}
        order={{ mainIds: ENTRIES.map((e) => e.id), pinnedIds: [] }}
        setOrder={() => {}}
        resetOrder={() => {}}
        mobile={{ open, onClose: () => setOpen(false), hand, onHandChange: setHand }}
      />
    </MemoryRouter>
  );
}

function menuButton() {
  // While the panel is open the modal hides the page behind it from assistive
  // tech -- the button included -- so look past that.
  return screen.getByRole("button", { name: "Open navigation", hidden: true });
}

describe("phone navigation", () => {
  afterEach(() => window.localStorage.removeItem(NAV_HAND_KEY));

  it("sits on the right until the user picks the left, and remembers it", () => {
    render(<PhoneShell />);
    expect(menuButton().dataset.hand).toBe("right");

    fireEvent.click(menuButton());
    const panel = document.getElementById(ADMIN_NAV_ID)!;
    expect(panel.className).toMatch(/AnchorRight/);
    fireEvent.click(within(panel).getByRole("button", { name: "Left" }));

    expect(menuButton().dataset.hand).toBe("left");
    expect(document.getElementById(ADMIN_NAV_ID)!.className).toMatch(/AnchorLeft/);
    expect(window.localStorage.getItem(NAV_HAND_KEY)).toBe("left");
  });

  it("shows every label, opens a group without leaving the page, and closes when a page is picked", () => {
    render(<PhoneShell />);
    fireEvent.click(menuButton());
    expect(menuButton()).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(ADMIN_NAV_ID)!;

    // Labels even though the desktop rail is collapsed (drawerOpen=false).
    expect(within(panel).getByText("Dashboard")).toBeInTheDocument();
    fireEvent.click(within(panel).getByText("LLM"));
    expect(menuButton()).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(within(panel).getByText("Providers"));
    expect(menuButton()).toHaveAttribute("aria-expanded", "false");
  });
});
