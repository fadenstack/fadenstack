import { describe, expect, it } from "vitest";

import { driverUi, isMacNode, isNativeNode } from "./driverCaps";

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

describe("isMacNode", () => {
  it("is a Mac by its native runtime or its OS, and Linux is not", () => {
    expect(isMacNode({ native_runtime: 1 })).toBe(true);
    expect(isMacNode({ os: "macOS-26.1-arm64-arm-64bit" })).toBe(true);
    expect(isMacNode({ os: "Darwin" })).toBe(true);
    expect(isMacNode({ os: "Linux-6.8.0-generic-x86_64-with-glibc2.39" })).toBe(false);
    expect(isMacNode({})).toBe(false);
    expect(isMacNode(null)).toBe(false);
  });
});
