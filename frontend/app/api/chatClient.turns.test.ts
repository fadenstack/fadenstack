/**
 * A streamed reply names its turn, and a failure is read to the stream's end.
 *
 * The gateway stores a failed reply before it closes the stream; a page that
 * gave up at the error event reloaded the chat before that, and found the
 * reply still "running".
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { retryTurn, streamChat } from "./chatClient";
import type { StreamDelta } from "./chatTypes";

function sse(...events: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(new TextEncoder().encode(`data: ${event}\n\n`));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream", "x-faden-turn-id": "turn-1", "x-langfuse-trace-id": "trace-1" },
  });
}

const chunk = (content: string) => JSON.stringify({ choices: [{ delta: { content } }] });

async function read(reader: AsyncGenerator<StreamDelta>): Promise<{ deltas: StreamDelta[]; error: Error | null }> {
  const deltas: StreamDelta[] = [];
  try {
    for await (const delta of reader) deltas.push(delta);
  } catch (error) {
    return { deltas, error: error as Error };
  }
  return { deltas, error: null };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a streamed reply", () => {
  it("names its turn and trace on the first delta", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse(chunk("Hel"), chunk("lo"), "[DONE]")));
    const { deltas, error } = await read(streamChat({ model: "m" }).reader);
    expect(error).toBeNull();
    expect(deltas[0]).toMatchObject({ content: "Hel", turn_id: "turn-1", trace_id: "trace-1" });
    expect(deltas[1]).toEqual({ content: "lo" });
  });

  it("throws an error event only once the stream has ended", async () => {
    let release!: () => void;
    const saved = new Promise<void>((resolve) => (release = resolve));
    vi.stubGlobal("fetch", vi.fn(async () => {
      const encode = (event: string) => new TextEncoder().encode(`data: ${event}\n\n`);
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encode(JSON.stringify({ error: { message: "System message must be at the beginning." } })));
          // The gateway sends [DONE] once the failed reply is stored.
          void saved.then(() => {
            controller.enqueue(encode("[DONE]"));
            controller.close();
          });
        },
      });
      return new Response(body, { headers: { "x-faden-turn-id": "turn-1" } });
    }));
    let settled = false;
    const reading = read(streamChat({ model: "m" }).reader).finally(() => (settled = true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(settled).toBe(false);

    release();
    const { error } = await reading;
    expect(error?.message).toBe("System message must be at the beginning.");
  });

  it("asks for a reply that runs on its own", async () => {
    const fetch = vi.fn(async () => sse(chunk("hi"), "[DONE]"));
    vi.stubGlobal("fetch", fetch);
    await read(streamChat({ model: "m", session_id: "s1" }).reader);
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({ stream: true, background: true, session_id: "s1" });
  });

  it("tries a reply again at its turn", async () => {
    const fetch = vi.fn(async () => sse(chunk("again"), "[DONE]"));
    vi.stubGlobal("fetch", fetch);
    await read(retryTurn("s1", "turn-0", { model: "other" }).reader);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat/sessions/s1/turns/turn-0/retry");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ model: "other" });
  });
});
