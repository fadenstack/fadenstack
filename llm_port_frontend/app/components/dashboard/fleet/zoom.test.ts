import { describe, expect, it } from "vitest";
import { cellsPerRow, foldGroups, groupLevel, loadBand } from "./zoom";

describe("semantic zoom", () => {
  it("shows cards for a fleet of up to four machines", () => {
    expect(groupLevel(3, 3, 600)).toBe("cards");
    expect(groupLevel(4, 2, 600)).toBe("cards");
  });

  it("shows cells while a group fits in three rows and has at most 100 machines", () => {
    const perRow = cellsPerRow(340); // 20 cells of 14 px + 3 px
    expect(perRow).toBe(20);
    expect(groupLevel(50, 50, 340)).toBe("cells");
    expect(groupLevel(80, 61, 340)).toBe("summary"); // 4 rows
    expect(groupLevel(200, 120, 2000)).toBe("summary"); // over 100 in one group
  });

  it("summarises everything above 250 machines", () => {
    expect(groupLevel(300, 3, 2000)).toBe("summary");
  });

  it("folds groups beyond twenty, keeping the ten with the most problems", () => {
    const groups = Array.from({ length: 25 }, (_, i) => ({ id: i, problems: i % 7 }));
    const { shown, folded } = foldGroups(groups, (g) => g.problems);
    expect(shown).toHaveLength(10);
    expect(folded).toHaveLength(15);
    expect(Math.min(...shown.map((g) => g.problems))).toBeGreaterThanOrEqual(Math.max(...folded.map((g) => g.problems)));
    expect(foldGroups(groups.slice(0, 20), (g) => g.problems).folded).toEqual([]);
  });

  it("puts loads in 20 % bands", () => {
    expect([0, 19.9, 20, 55, 80, 100, 130].map(loadBand)).toEqual([0, 0, 1, 2, 4, 4, 4]);
    expect(loadBand(null)).toBeNull();
  });
});
