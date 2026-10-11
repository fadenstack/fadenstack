/**
 * Tool modes in the chat: the user picks what tools may do, and a tool that
 * must ask waits for them in the answer (the classes: gateway/faden_gateway/services/gateway/tool_classes.py).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "~/api/chatClient";
import * as toolsApi from "~/api/tools";
import { readFadenEvent } from "../hooks/useChatStream";
import ToolPanel from "./ToolPanel";

function catalog(overrides: Partial<toolsApi.ToolAvailabilityResponse> = {}): toolsApi.ToolAvailabilityResponse {
  const tool = (id: string, cls: toolsApi.ToolClass, decision: toolsApi.ToolModeDecision) => ({
    tool_id: id, display_name: id, description: null, realm: "mcp_remote" as const, source: "mcp" as const,
    effective_enabled: decision !== "hidden", policy_allowed: true, user_enabled: true, available: true,
    availability_reason: decision === "hidden" ? "tool_mode_read_only" : null,
    tool_class: cls, mode_decision: decision,
    mode_reason: decision === "hidden" ? "Only read-only tools are allowed in this chat." : null,
  });
  return {
    session_id: "s1", execution_mode: "server_only", effective_catalog_version: 0,
    tools: [tool("mcp.gh.get_issue", "read", "run"), tool("mcp.gh.delete_repo", "destructive", "hidden")],
    tool_mode: "read_only", effective_tool_mode: "read_only", tool_mode_ceiling: null,
    ...overrides,
  };
}

describe("the tool panel's mode", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows each tool's class, greys out what the mode hides, and saves a new mode for the chat", async () => {
    vi.spyOn(toolsApi, "getAvailableTools").mockResolvedValue(catalog());
    const patch = vi.spyOn(toolsApi, "patchSessionToolPolicy").mockResolvedValue({} as toolsApi.SessionToolPolicy);
    render(<ToolPanel sessionId="s1" executionMode="server_only" />);
    await screen.findByText("delete_repo");
    expect(document.querySelector('[data-tool-class="destructive"]')).toBeTruthy();
    expect(screen.getByText("Only read-only tools are allowed in this chat.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("s1", { tool_mode: "ask" }));
  });

  it("offers no mode above the ceiling", async () => {
    vi.spyOn(toolsApi, "getAvailableTools").mockResolvedValue(
      catalog({ tool_mode: "auto", effective_tool_mode: "ask", tool_mode_ceiling: "ask" }),
    );
    render(<ToolPanel sessionId="s1" executionMode="server_only" />);
    await screen.findByText("Your organisation allows at most this mode.", { exact: false });
    expect((screen.getByRole("button", { name: "Auto" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("a tool that waits for the user", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("arrives as a named event beside the answer, which goes on", async () => {
    const body =
      'data: {"choices":[{"delta":{"content":"Let me check."}}]}\n\n' +
      'event: faden\ndata: {"type":"CUSTOM","name":"faden.approval_required","value":' +
      '{"approvalId":"a1","toolCallName":"mcp.gh.create_issue","message":"Needs your OK.","timeoutS":300}}\n\n' +
      'data: {"choices":[{"delta":{"content":" Done."}}]}\n\ndata: [DONE]\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const seen: unknown[] = [];
    for await (const delta of streamChat({ model: "m", messages: [] }).reader) seen.push(delta);
    const text = seen.map((d) => (d as { content?: string }).content ?? "").join("");
    expect(text).toBe("Let me check. Done.");
    const event = seen.find((d) => (d as { faden?: unknown }).faden) as { faden: Parameters<typeof readFadenEvent>[0] };
    expect(readFadenEvent(event.faden)).toEqual({
      kind: "approval",
      approval: { approvalId: "a1", toolName: "mcp.gh.create_issue", message: "Needs your OK.", timeoutS: 300 },
    });
  });

  it("reads a refused tool as a notice", () => {
    expect(readFadenEvent({ type: "CUSTOM", name: "faden.tool_denied",
      value: { toolCallName: "mcp.gh.delete_repo", message: "Only read-only tools are allowed in this chat." } }))
      .toEqual({ kind: "denied", notice: "mcp.gh.delete_repo: Only read-only tools are allowed in this chat." });
    expect(readFadenEvent({ type: "CUSTOM", name: "faden.citations", value: {} })).toBeNull();
  });
});
