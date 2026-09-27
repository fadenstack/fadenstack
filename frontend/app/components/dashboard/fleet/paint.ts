/**
 * How a machine is drawn: by health (its reported state) or by load (GPU
 * utilization, VRAM allocated). Health states differ in shape as well as
 * colour -- offline hollow, stale dashed, error thick-bordered, maintenance
 * hatched -- so colour is never the only signal. Load uses the neutral scale:
 * a busy GPU is not a fault.
 */
import type { Theme } from "@mui/material/styles";
import type { CapacityMachine, FleetColorBy, MachineHealth } from "~/api/dashboard";
import { loadBand } from "./zoom";

export const HEALTH_LABEL_KEYS: Record<MachineHealth, string> = {
  ok: "dashboard.machine_health.ok",
  degraded: "dashboard.machine_health.degraded",
  error: "dashboard.machine_health.error",
  maintenance: "dashboard.machine_health.maintenance",
  offline: "dashboard.machine_health.offline",
  stale: "dashboard.machine_health.stale",
};

/** The value a colour-by mode reads from a machine. */
export function loadOf(machine: CapacityMachine, colorBy: FleetColorBy): number | null {
  if (colorBy === "gpu_util") return machine.gpu_util_pct;
  if (colorBy === "vram_allocated") return machine.vram_allocated_pct;
  return null;
}

/** Styles for one cell (or a health dot) of *machine* under *colorBy*. */
export function cellSx(machine: CapacityMachine, colorBy: FleetColorBy, theme: Theme): Record<string, unknown> {
  if (colorBy === "health") return healthSx(machine.health, theme);
  const band = loadBand(loadOf(machine, colorBy));
  if (band === null) {
    // No figure: an empty dashed cell, not a colour that means something.
    return { border: `1px dashed ${theme.palette.text.disabled}`, bgcolor: "transparent" };
  }
  return { bgcolor: theme.palette.dashboard.load[band], border: `1px solid ${theme.palette.divider}` };
}

export function healthSx(health: MachineHealth, theme: Theme): Record<string, unknown> {
  const color = theme.palette.dashboard.health[health];
  switch (health) {
    case "offline":
      return { bgcolor: "transparent", border: `2px solid ${color}` };
    case "stale":
      return { bgcolor: "transparent", border: `2px dashed ${color}` };
    case "error":
      return { bgcolor: color, boxShadow: `inset 0 0 0 2px ${theme.palette.background.paper}`, outline: `2px solid ${color}` };
    case "maintenance":
      return {
        backgroundImage: `repeating-linear-gradient(45deg, ${color} 0 3px, transparent 3px 6px)`,
        border: `1px solid ${color}`,
      };
    default:
      return { bgcolor: color };
  }
}
