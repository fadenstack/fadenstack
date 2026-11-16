/** Turning an agent off and on: the right addresses, POST, and the server's own text when it refuses. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { agentsApi } from "./agents";

afterEach(() => vi.restoreAllMocks());

function answer(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

describe("turning an agent off and on", () => {
  it("POSTs to turn-off", async () => {
    const fetch = answer(200, { slug: "excel-assistant", enabled: false });
    expect(await agentsApi.turnOff("excel-assistant")).toMatchObject({ enabled: false });
    const [url, init] = fetch.mock.calls[0];
    expect(String(url)).toBe("/api/agents/excel-assistant/turn-off");
    expect(init?.method).toBe("POST");
  });

  it("POSTs to turn-on", async () => {
    const fetch = answer(200, { slug: "excel-assistant", enabled: true });
    await agentsApi.turnOn("excel-assistant");
    const [url, init] = fetch.mock.calls[0];
    expect(String(url)).toBe("/api/agents/excel-assistant/turn-on");
    expect(init?.method).toBe("POST");
  });

  it("carries the server's text when turning on is refused", async () => {
    const detail = "The agent stays off: the gateway could not end its grants from before. Try again in a moment.";
    answer(503, { detail });
    await expect(agentsApi.turnOn("excel-assistant")).rejects.toMatchObject({ message: detail, status: 503 });
  });
});
