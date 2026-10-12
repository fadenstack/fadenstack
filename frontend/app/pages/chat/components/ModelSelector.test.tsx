/**
 * The model picker is a control a screen reader must be able to name, in both forms.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import ModelSelector from "./ModelSelector";

describe("ModelSelector", () => {
  beforeEach(() => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([
      { alias: "qwen3.8-27b-fp8", description: null, enabled: true },
    ]);
  });

  it.each([false, true])("is named \"Model\" (compact: %s)", async (compact) => {
    render(
      <MemoryRouter>
        <ModelSelector value="qwen3.8-27b-fp8" onChange={() => {}} compact={compact} />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("combobox", { name: "Model" })).toBeInTheDocument();
  });
});
