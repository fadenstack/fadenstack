/**
 * A refused prompt or answer is a notice on its turn, not an error box.
 *
 * The gateway refuses with 422 policy_blocked and a faden.governance notice;
 * the session keeps the turn without its text (status blocked_input), and the
 * page offers "edit and resend" -- the text is still in this browser.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatApiError, streamChat } from "~/api/chatClient";
import { errorDetail } from "../hooks/useChatStream";
import MessageBubble from "./MessageBubble";

describe("the chat's errors, by kind", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads a refused prompt from the stream's error event", async () => {
    const body =
      'data: {"error": {"message": "Not here.", "type": "governance_error", "code": "policy_blocked", ' +
      '"status": 422}, "faden": {"governance": {"outcome": "prompt_blocked", "stage": "input", ' +
      '"message": "Project names stay out.", "decision_id": "d1"}}}\n\ndata: [DONE]\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const { reader } = streamChat({ model: "m", messages: [] });
    const failed = await reader.next().catch((e: unknown) => e);
    expect(failed).toBeInstanceOf(ChatApiError);
    const detail = errorDetail(failed, "the merger code is BLUEBIRD");
    expect(detail).toEqual({
      kind: "blocked",
      message: "Project names stay out.",
      text: "the merger code is BLUEBIRD",
    });
  });

  it("says when to retry a rate limit", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ error: { message: "slow down", type: "rate_limit_error", code: "rate_limit_rpm" } }),
            { status: 429, headers: { "Retry-After": "17" } },
          ),
      ),
    );
    const { reader } = streamChat({ model: "m", messages: [] });
    const failed = await reader.next().catch((e: unknown) => e);
    expect(errorDetail(failed)).toMatchObject({ kind: "rate_limit", retryAfter: 17 });
  });

  it("tells a used-up budget from a rate limit, with the kind of budget and when it starts again", async () => {
    const message = "This would go over your team's budget for this period.";
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ error: { message, type: "insufficient_quota", code: "budget_exceeded", param: "team" } }),
            { status: 429, headers: { "Retry-After": "86400", "x-should-retry": "false" } },
          ),
      ),
    );
    const { reader } = streamChat({ model: "m", messages: [] });
    const failed = await reader.next().catch((e: unknown) => e);
    // No "Rate limit reached (429)" in front: the chat words it as a budget, from the code and the kind.
    expect(errorDetail(failed)).toEqual({ kind: "budget", message, retryAfter: 86400, param: "team" });
  });

  it("reads a used-up budget from the stream's error event too", async () => {
    const body =
      'data: {"error": {"message": "This would go over this agent\'s budget.", "type": "insufficient_quota", ' +
      '"code": "budget_exceeded", "param": "agent", "status": 429}}\n\ndata: [DONE]\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const { reader } = streamChat({ model: "m", messages: [] });
    const failed = await reader.next().catch((e: unknown) => e);
    expect(errorDetail(failed)).toMatchObject({ kind: "budget", param: "agent" });
  });

  it("keeps anything else an error", () => {
    expect(errorDetail(new Error("boom")).kind).toBe("error");
  });
});

describe("a refused turn in the history", () => {
  it("shows the notice in place of the text", () => {
    render(<MessageBubble role="user" content="" status="blocked_input" notice="Project names stay out." />);
    expect(screen.getByText("Not sent")).toBeTruthy();
    expect(screen.getByText("Project names stay out.")).toBeTruthy();
  });

  it("shows a withheld answer as a notice, without its footer", () => {
    const { container } = render(
      <MessageBubble role="assistant" content="" status="blocked_output" notice="Withheld." />,
    );
    expect(screen.getByText("Answer withheld")).toBeTruthy();
    expect(container.querySelector('[data-message-status="blocked_output"]')).toBeTruthy();
  });

  it("marks a failed turn", () => {
    const { container } = render(<MessageBubble role="user" content="Hello?" status="failed" />);
    expect(screen.getByText("Hello?")).toBeTruthy();
    expect(container.querySelector('[data-message-status="failed"]')).toBeTruthy();
  });
});
