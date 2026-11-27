import { describe, expect, it } from "vitest";

import { driverUi, isNativeNode } from "./driverCaps";

describe("driverUi", () => {
  it("hides Ray-only controls for a standalone environment", () => {
    expect(driverUi("standalone")).toEqual({ network: false, drain: false, multiNode: false, scaling: false });
  });
  it("keeps everything for ray and for a driver not yet known", () => {
    expect(driverUi("ray").network).toBe(true);
    expect(driverUi(null).drain).toBe(true);
  });
  it("reads a native node from its capability", () => {
    expect(isNativeNode({ native_runtime: 1 })).toBe(true);
    expect(isNativeNode({})).toBe(false);
    expect(isNativeNode(undefined)).toBe(false);
  });
});
