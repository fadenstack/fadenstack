import { describe, expect, it } from "vitest";
import { fmtChange } from "./format";

describe("a trend's change", () => {
  it("is a percentage with one decimal below +100 %", () => {
    expect(fmtChange(0.123, "en")).toBe("+12.3 %");
    expect(fmtChange(-0.5, "en")).toBe("−50 %");
    expect(fmtChange(0, "en")).toBe("+0 %");
  });

  it("drops the decimal from +100 %", () => {
    expect(fmtChange(1.936, "en")).toBe("+194 %");
    expect(fmtChange(8.99, "en")).toBe("+899 %");
  });

  it("is a factor from ten times the baseline", () => {
    expect(fmtChange(9, "en")).toBe("10×");
    expect(fmtChange(11.46, "en")).toBe("12.5×");
    expect(fmtChange(617.474, "en")).toBe("618×");
    expect(fmtChange(9999, "en")).toBe("10,000×");
  });
});
