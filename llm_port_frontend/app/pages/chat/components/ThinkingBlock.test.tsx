/**
 * A reasoning model's thinking is shown while it thinks, and folded once it answers.
 *
 * With OpenRouter's reasoning models the chat showed nothing for 1-14 s: the
 * gateway dropped the thinking on the path chats take, and the page did not
 * read it where it did arrive.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "~/api/chatClient";
import type { StreamDelta } from "~/api/chatTypes";
import ThinkingBlock from "./ThinkingBlock";

describe("ThinkingBlock", () => {
  it("shows the thinking as it comes", () => {
    render(<ThinkingBlock text="The user wants three sentences." live />);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    expect(screen.getByText("The user wants three sentences.")).toBeInTheDocument();
  });

  it("folds it into how long it took, to open on demand", () => {
    render(<ThinkingBlock text="The user wants three sentences." ms={2600} />);
    const toggle = screen.getByRole("button", { name: /thought for 3 s/i });
    expect(screen.queryByText("The user wants three sentences.")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("The user wants three sentences.")).toBeInTheDocument();
  });
});

function sse(...events: Record<string, unknown>[]): Response {
  const body = [...events.map((e) => `data: ${JSON.stringify(e)}\n\n`), "data: [DONE]\n\n"].join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("streamChat", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the thinking under either name, apart from the answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sse(
      { choices: [{ index: 0, delta: { reasoning_content: "The user " } }] },
      { choices: [{ index: 0, delta: { reasoning: "wants three." } }] },
      { choices: [{ index: 0, delta: { content: "A plan." } }] },
    )));
    const deltas: StreamDelta[] = [];
    for await (const delta of streamChat({ model: "m", messages: [] }).reader) deltas.push(delta);
    expect(deltas.map((d) => d.reasoning ?? "").join("")).toBe("The user wants three.");
    expect(deltas.map((d) => d.content ?? "").join("")).toBe("A plan.");
  });
});
