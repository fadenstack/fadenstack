/**
 * A console left open during an update reloads into the maintenance page --
 * but never for a service that is merely down, which would loop.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { followMaintenance, resetFollowMaintenance } from "./maintenance";

const realFetch = window.fetch;

function answer(status: number, header?: string) {
  const headers = new Headers(header ? { "X-Faden-Maintenance": header } : {});
  window.fetch = vi.fn(() => Promise.resolve(new Response("{}", { status, headers })));
}

describe("followMaintenance", () => {
  beforeEach(() => resetFollowMaintenance());
  afterEach(() => {
    window.fetch = realFetch;
  });

  it("reloads once at the first call refused for maintenance", async () => {
    answer(503, "maintenance");
    const reload = vi.fn();
    followMaintenance(reload);

    const response = await fetch("/api/admin/dashboard/attention");
    await fetch("/api/admin/dashboard/overview");

    expect(response.status).toBe(503);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload for a service that is down, or for any other answer", async () => {
    const reload = vi.fn();
    answer(503, "unavailable");
    followMaintenance(reload);
    await fetch("/api/x");
    expect(reload).not.toHaveBeenCalled();
  });

  it("passes ordinary answers through untouched", async () => {
    answer(200);
    const reload = vi.fn();
    followMaintenance(reload);
    expect((await fetch("/api/x")).status).toBe(200);
    expect(reload).not.toHaveBeenCalled();
  });
});
