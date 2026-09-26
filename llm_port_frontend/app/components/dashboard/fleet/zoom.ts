/**
 * Semantic zoom for the capacity widget (spec §6.4.2): the same widget from
 * one machine to a thousand. Each group (a cluster, or the machines in none)
 * is shown at the most detailed level that still fits:
 *
 * - cards:   the whole fleet has at most 4 machines -- a card each;
 * - cells:   the group fits in 3 rows of 14 px cells and has at most 100;
 * - summary: otherwise, or whenever the fleet has over 250 machines --
 *            health counts, a load histogram, and the machines that need a look.
 *
 * With more than 20 groups, the 10 with the most problems are shown and the
 * rest fold into one "other groups" row.
 */

export const CARDS_MAX_MACHINES = 4;
export const CELLS_MAX_PER_GROUP = 100;
export const CELLS_MAX_ROWS = 3;
export const SUMMARY_ABOVE_MACHINES = 250;
export const GROUPS_BEFORE_FOLDING = 20;
export const GROUPS_SHOWN_WHEN_FOLDED = 10;
export const CELL_PX = 14;
export const CELL_GAP_PX = 3;

export type Level = "cards" | "cells" | "summary";

export function fleetUsesCards(totalMachines: number): boolean {
  return totalMachines > 0 && totalMachines <= CARDS_MAX_MACHINES;
}

/** Cells of CELL_PX with CELL_GAP_PX between them in *width* pixels. */
export function cellsPerRow(width: number): number {
  return Math.max(1, Math.floor((width + CELL_GAP_PX) / (CELL_PX + CELL_GAP_PX)));
}

export function groupLevel(totalMachines: number, groupSize: number, width: number): Level {
  if (fleetUsesCards(totalMachines)) return "cards";
  if (totalMachines > SUMMARY_ABOVE_MACHINES) return "summary";
  const rows = Math.ceil(groupSize / cellsPerRow(width));
  return groupSize <= CELLS_MAX_PER_GROUP && rows <= CELLS_MAX_ROWS ? "cells" : "summary";
}

/** The groups to show, and the ones folded into "other groups" (most problems first). */
export function foldGroups<T>(groups: T[], problems: (g: T) => number): { shown: T[]; folded: T[] } {
  if (groups.length <= GROUPS_BEFORE_FOLDING) return { shown: groups, folded: [] };
  const ranked = [...groups].sort((a, b) => problems(b) - problems(a));
  return { shown: ranked.slice(0, GROUPS_SHOWN_WHEN_FOLDED), folded: ranked.slice(GROUPS_SHOWN_WHEN_FOLDED) };
}

/** Which of the five 20 % bands a load falls in; null without a figure. */
export function loadBand(value: number | null | undefined): 0 | 1 | 2 | 3 | 4 | null {
  if (value == null || Number.isNaN(value)) return null;
  return Math.min(4, Math.max(0, Math.floor(value / 20))) as 0 | 1 | 2 | 3 | 4;
}
