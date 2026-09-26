import { describe, expect, it } from "vitest";
import { fillingIds, packRows } from "./DashboardGrid";

describe("rows", () => {
  it("are packed in order, a new row when the next width does not fit", () => {
    // XL | L + S | M + M | M (alone) | S + S + S
    expect(packRows([12, 8, 4, 6, 6, 6, 4, 4, 4])).toEqual([[0], [1, 2], [3, 4], [5, 6], [7, 8]]);
  });
});

describe("a fill widget", () => {
  it("takes its row's height from a content widget beside it", () => {
    const filling = fillingIds([
      { id: "traffic", columns: 8 },
      { id: "attention", columns: 4, fit: "fill" },
      { id: "capacity", columns: 6 },
      { id: "usage", columns: 6, fit: "fill" },
    ]);
    expect([...filling]).toEqual(["attention", "usage"]);
  });

  it("sizes to its content alone, or among fill widgets only", () => {
    expect(fillingIds([{ id: "attention", columns: 12, fit: "fill" }]).size).toBe(0);
    expect(
      fillingIds([
        { id: "usage", columns: 6, fit: "fill" },
        { id: "attention", columns: 6, fit: "fill" },
      ]).size,
    ).toBe(0);
  });
});
