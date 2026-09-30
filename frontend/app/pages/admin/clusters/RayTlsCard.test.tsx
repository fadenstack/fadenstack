/**
 * Ray TLS switched on a cluster: while it restarts the card looks again now
 * and then; a stopped cluster takes the switch when it starts, and the card
 * says so instead of polling until it does.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type RayTlsStatus } from "~/api/inference";

import RayTlsCard from "./RayTlsCard";

const ENV = "89ed5f75-078c-4d9a-8ac7-09255fe29db6";

function status(overrides: Partial<RayTlsStatus> = {}): RayTlsStatus {
  return {
    on: true,
    applied: false,
    restarting: true,
    running: { "10.250.10.49": false, "10.250.10.71": false },
    machines: 2,
    old_agents: [],
    can_switch_on: true,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("RayTlsCard", () => {
  it("looks again while the cluster restarts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const asked = vi.spyOn(inferenceApi, "rayTls").mockResolvedValue(status());
    render(<RayTlsCard environmentId={ENV} />);

    expect(await screen.findByText("Restarting")).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(5_100);
    expect(asked.mock.calls.length).toBeGreaterThan(1);
  });

  it("says a stopped cluster takes it when it starts, and does not poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const asked = vi.spyOn(inferenceApi, "rayTls").mockResolvedValue(status({ restarting: false }));
    render(<RayTlsCard environmentId={ENV} />);

    expect(await screen.findByText("Applies when the cluster starts")).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(asked).toHaveBeenCalledTimes(1);
  });
});
