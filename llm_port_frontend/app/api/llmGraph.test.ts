import { afterEach, describe, expect, it, vi } from "vitest";

import { getLlmGraphTraces, traceStreamAvailable } from "./llmGraph";

afterEach(() => vi.unstubAllGlobals());

describe("traceStreamAvailable", () => {
  it("is no where the stream is an Enterprise feature (402)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 402 })));
    await expect(traceStreamAvailable()).resolves.toBe(false);
  });

  it("is yes where the stream opens, and does not keep it open", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init: RequestInit) => {
        signal = init.signal ?? undefined;
        return Promise.resolve(new Response("", { status: 200 }));
      }),
    );
    await expect(traceStreamAvailable()).resolves.toBe(true);
    expect(signal?.aborted).toBe(true);
  });

  it("keeps trying the stream when the question itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    await expect(traceStreamAvailable()).resolves.toBe(true);
  });
});

describe("getLlmGraphTraces", () => {
  it("carries on from the server's cursor exactly", async () => {
    // An event id is about 1.8e21: as a number it went out as "1.79e+21",
    // and every fetch after the first was refused with 422.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await getLlmGraphTraces(100, "1790290906001035812345");
    expect(String(fetchMock.mock.calls[0][0])).toContain("after_event_id=1790290906001035812345");
  });
});
