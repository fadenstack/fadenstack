import { describe, expect, it } from "vitest";

import { hardwareSummary } from "./NodeOnboardingPage";

// What a DGX Spark's agent sends when it asks to join: the GPU's name per device.
const DGX_SPARK = {
  gpu_count: 1,
  machine: "aarch64",
  gpu: { count: 1, devices: [{ name: "NVIDIA GB10", vendor: "nvidia", memory_total_mib: 0 }] },
};

describe("hardwareSummary", () => {
  it("names the GPU the agent reports per device", () => {
    // It said "1 × accelerator · aarch64" for both DGX Sparks on .61.
    expect(hardwareSummary(DGX_SPARK)).toBe("1 × NVIDIA GB10 · aarch64");
  });

  it("still reads a name given at the top level", () => {
    expect(hardwareSummary({ gpu_count: 2, machine: "x86_64", gpu: { family: "RTX 6000" } })).toBe("2 × RTX 6000 · x86_64");
  });
});
