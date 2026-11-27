/**
 * The composer's model and thinking pills: the ladder the model offers, what is kept per person and
 * model, and what the composer tells the window to send.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ModelAlias } from "~/api/chatTypes";
import { preferences } from "~/api/preferences";
import { resetThinkingCache } from "../hooks/useChatThinking";
import ChatInput from "./ChatInput";

function model(alias: string, reasoning_levels: ModelAlias["reasoning_levels"]): ModelAlias {
  return { alias, description: null, enabled: true, kind: "chat", reasoning_levels };
}

function renderInput(selectedModel: string, onReasoningChange = vi.fn()) {
  render(
    <MemoryRouter>
      <ChatInput
        onSend={vi.fn()}
        selectedModel={selectedModel}
        onModelChange={() => {}}
        autoFocus={false}
        onReasoningChange={onReasoningChange}
      />
    </MemoryRouter>,
  );
  return onReasoningChange;
}

async function pill() {
  return screen.findByTestId("thinking-pill");
}

describe("ChatInput thinking pill", () => {
  beforeEach(() => {
    resetThinkingCache();
    vi.spyOn(preferences, "get").mockResolvedValue({ preferences: {} });
    vi.spyOn(preferences, "update").mockResolvedValue({ preferences: {} });
  });

  it("offers Auto, Off, Low, Medium and High when the model's levels are not known", async () => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", null)]);
    renderInput("m");
    fireEvent.click(await pill());
    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Auto", "Off", "Low", "Medium", "High"]);
    expect(await pill()).toHaveTextContent("Thinking: Auto");
  });

  it("offers only what the model tells apart, and Auto always", async () => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", ["low", "high"])]);
    renderInput("m");
    fireEvent.click(await pill());
    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["Auto", "Low", "High"]);
  });

  it("hides the pill for a model with no levels", async () => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", [])]);
    renderInput("m");
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveTextContent("m"));
    expect(screen.queryByTestId("thinking-pill")).toBeNull();
  });

  it("keeps the choice per model and tells the window: Off is none, High is high", async () => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", null)]);
    const onChange = renderInput("m");
    fireEvent.click(await pill());
    fireEvent.click(within(screen.getByRole("menu")).getByText("Off"));
    expect(preferences.update).toHaveBeenCalledWith({ chat_thinking: { m: "off" } });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("none"));

    fireEvent.click(await pill());
    fireEvent.click(within(screen.getByRole("menu")).getByText("High"));
    expect(preferences.update).toHaveBeenLastCalledWith({ chat_thinking: { m: "high" } });
    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith("high"));
  });

  it("starts from what the person kept for the model", async () => {
    vi.spyOn(preferences, "get").mockResolvedValue({ preferences: { chat_thinking: { m: "medium" } } });
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", null)]);
    const onChange = renderInput("m");
    await waitFor(async () => expect(await pill()).toHaveTextContent("Thinking: Medium"));
    expect(onChange).toHaveBeenLastCalledWith("medium");
  });

  it("sends nothing and shows Auto when the kept level is not offered by the model", async () => {
    vi.spyOn(preferences, "get").mockResolvedValue({ preferences: { chat_thinking: { m: "medium" } } });
    vi.spyOn(chatApi, "listModels").mockResolvedValue([model("m", ["low", "high"])]);
    const onChange = renderInput("m");
    expect(await pill()).toHaveTextContent("Thinking: Auto");
    await waitFor(() => expect(preferences.get).toHaveBeenCalled());
    expect(onChange).not.toHaveBeenCalledWith("medium");
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});
