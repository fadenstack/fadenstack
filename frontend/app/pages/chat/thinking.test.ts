/**
 * The thinking ladder: which levels a model offers, what the kept level becomes, what goes on the wire.
 */
import { describe, expect, it } from "vitest";

import type { ModelAlias } from "~/api/chatTypes";
import { effectiveLevel, hasThinkingChoice, offeredLevels, reasoningEffort } from "./thinking";

function model(reasoning_levels: ModelAlias["reasoning_levels"]): ModelAlias {
  return { alias: "m", description: null, enabled: true, kind: "chat", reasoning_levels };
}

describe("thinking levels", () => {
  it("offers all five when the model's list does not say", () => {
    expect(offeredLevels(model(null))).toEqual(["auto", "off", "low", "medium", "high"]);
    expect(offeredLevels(model(undefined))).toEqual(["auto", "off", "low", "medium", "high"]);
  });

  it("offers Auto and only the levels the model tells apart, in the ladder's order", () => {
    expect(offeredLevels(model(["high", "off"]))).toEqual(["auto", "off", "high"]);
  });

  it("hides the pill for a model with no levels, or none known yet", () => {
    expect(hasThinkingChoice(model([]))).toBe(false);
    expect(hasThinkingChoice(null)).toBe(false);
    expect(hasThinkingChoice(model(["low"]))).toBe(true);
  });

  it("falls back to Auto when the kept level is not offered by the model", () => {
    expect(effectiveLevel(model(["low", "high"]), "medium")).toBe("auto");
    expect(effectiveLevel(model(["low", "high"]), "high")).toBe("high");
    expect(effectiveLevel(model(null), undefined)).toBe("auto");
  });

  it("sends nothing for Auto, none for Off and the level's name otherwise", () => {
    expect(reasoningEffort("auto")).toBeUndefined();
    expect(reasoningEffort("off")).toBe("none");
    expect(reasoningEffort("low")).toBe("low");
    expect(reasoningEffort("medium")).toBe("medium");
    expect(reasoningEffort("high")).toBe("high");
  });
});
