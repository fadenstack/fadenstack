/**
 * The chat's summary: its text, the model and time, and the fixed label that an AI wrote it.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatSummary } from "~/api/chatTypes";
import SummaryDialog from "./SummaryDialog";

const LABEL = "Summary written by AI. It can leave out or get wrong what was said.";

function summary(extra: Partial<ChatSummary> = {}): ChatSummary {
  return {
    id: "sum1", session_id: "s1", summary_text: "The two agreed on a date.\n\nNothing else was settled.",
    last_message_id: "m9", token_estimate: 20, created_at: "2026-11-20T09:30:00Z",
    model_alias: "qwen-summary", record_id: null, ...extra,
  };
}

describe("SummaryDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the text as written, with the model, the time and the AI label", async () => {
    vi.spyOn(chatApi, "getSummary").mockResolvedValue(summary());
    render(<SummaryDialog open sessionId="s1" onClose={() => {}} />);

    const text = await screen.findByTestId("summary-text");
    expect(text).toHaveTextContent("The two agreed on a date.");
    expect(text).toHaveAttribute("dir", "auto");
    expect(screen.getByTestId("summary-ai-label")).toHaveTextContent(LABEL);
    expect(screen.getByTestId("summary-meta")).toHaveTextContent("Model: qwen-summary");
    expect(screen.getByTestId("summary-meta")).toHaveTextContent(/Written .*2026/);
    expect(screen.queryByTestId("summary-marked")).toBeNull();
    expect(chatApi.getSummary).toHaveBeenCalledWith("s1");
  });

  it("says it is marked as AI-written when the summary has a record", async () => {
    vi.spyOn(chatApi, "getSummary").mockResolvedValue(summary({ record_id: "rec-1" }));
    render(<SummaryDialog open sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByTestId("summary-marked")).toHaveTextContent("Marked as AI-written");
  });

  it("says so when the chat has no summary, and when it could not be loaded", async () => {
    vi.spyOn(chatApi, "getSummary").mockResolvedValueOnce(null);
    const { unmount } = render(<SummaryDialog open sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("This chat has no summary.")).toBeInTheDocument();
    unmount();

    vi.spyOn(chatApi, "getSummary").mockRejectedValueOnce(new Error("500"));
    render(<SummaryDialog open sessionId="s1" onClose={() => {}} />);
    expect(await screen.findByText("The summary could not be loaded.")).toBeInTheDocument();
  });

  it("asks for nothing while it is closed", () => {
    vi.spyOn(chatApi, "getSummary").mockResolvedValue(summary());
    render(<SummaryDialog open={false} sessionId="s1" onClose={() => {}} />);
    expect(chatApi.getSummary).not.toHaveBeenCalled();
  });
});
