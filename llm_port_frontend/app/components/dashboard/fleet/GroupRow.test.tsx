import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ThemeProvider } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";
import type { CapacityGroup, CapacityMachine, MachineHealth } from "~/api/dashboard";
import { getAppTheme } from "~/theme";
import GroupRow from "./GroupRow";

function machine(i: number, health: MachineHealth): CapacityMachine {
  return {
    id: `m${i}`,
    name: `spark-${i}`,
    health,
    last_seen: null,
    cpu_pct: 10,
    gpu_util_pct: 99,
    vram_allocated_pct: 90,
    gpus: [],
  };
}

function group(machines: CapacityMachine[], withMachines = true): CapacityGroup {
  const counts = { ok: 0, degraded: 0, error: 0, maintenance: 0, offline: 0, stale: 0 };
  for (const m of machines) counts[m.health] += 1;
  return {
    kind: "cluster",
    id: "c1",
    name: "DGX Pair",
    status: "ready",
    observed_at: null,
    stale: false,
    machines_total: machines.length,
    machines_online: machines.filter((m) => m.health !== "offline" && m.health !== "stale").length,
    gpus_total: 2,
    gpus_free: 1,
    health_counts: counts,
    load_histogram: { gpu_util: [0, 0, 0, 0, machines.length], vram_allocated: [0, 0, 0, 0, machines.length] },
    exceptions: machines.filter((m) => m.health !== "ok").map(({ id, name, health }) => ({ id, name, health })),
    machines: withMachines ? machines : null,
    next_cursor: null,
  };
}

function renderRow(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <ThemeProvider theme={getAppTheme("dark")}>
        <ul>{ui}</ul>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("a group row", () => {
  it("is one button that names the group and its problems, with the cells hidden from assistive tech", () => {
    const onOpen = vi.fn();
    const g = group([machine(1, "ok"), machine(2, "degraded"), machine(3, "offline")]);
    const { container } = renderRow(<GroupRow group={g} name="DGX Pair" level="cells" colorBy="health" onOpen={onOpen} />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toMatch(/DGX Pair/);
    fireEvent.click(buttons[0]);
    expect(onOpen).toHaveBeenCalledOnce();
    // Three cells, all inside the aria-hidden drawing.
    const drawing = container.querySelector('[aria-hidden="true"]:not(svg)');
    expect(drawing?.querySelectorAll("div > div > div").length).toBeGreaterThanOrEqual(3);
  });

  it("summarises a large group and names the machines that need a look", () => {
    const g = group([machine(1, "ok"), machine(2, "error")], false);
    renderRow(<GroupRow group={g} name="DGX Pair" level="summary" colorBy="gpu_util" onOpen={() => {}} />);

    expect(screen.getByText(/spark-2/)).toBeTruthy();
  });

  it("has no button when there is no list to open (folded groups)", () => {
    renderRow(<GroupRow group={group([machine(1, "ok")], false)} name="Other groups: 3" level="summary" colorBy="health" />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
