/** The approval levels in the editor: Bypass can be the highest level, never the one a conversation starts in. */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_DEFINITION } from "./agentDefinition";
import { AgentDefinitionEditor } from "./AgentDefinitionEditor";

vi.mock("~/api/about", () => ({ hasFeature: vi.fn().mockResolvedValue(false) }));
vi.mock("~/api/chatClient", () => ({ chatApi: { listModels: vi.fn().mockResolvedValue([]) } }));
vi.mock("~/api/rag", () => ({ ragLite: { listCollections: vi.fn().mockResolvedValue([]) } }));

const optionNames = async (label: string) => {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  const names = within(await screen.findByRole("listbox"))
    .getAllByRole("option")
    .map((o) => o.textContent);
  await userEvent.keyboard("{Escape}");
  return names;
};

describe("AgentDefinitionEditor approval levels", () => {
  it("offers Bypass for the highest level only", async () => {
    render(<AgentDefinitionEditor value={EMPTY_DEFINITION} onChange={vi.fn()} />);

    expect(await optionNames("At most")).toEqual(["Off", "Read only", "Ask", "Auto", "Bypass"]);
    expect(await optionNames("Starts in")).toEqual(["Off", "Read only", "Ask", "Auto"]);
    expect(screen.getByText(/can be published only while an administrator allows Bypass/)).toBeInTheDocument();
  });

  it("saves the choice of Bypass as the highest level", async () => {
    const onChange = vi.fn();
    render(<AgentDefinitionEditor value={EMPTY_DEFINITION} onChange={onChange} />);

    await userEvent.click(screen.getByRole("combobox", { name: "At most" }));
    await userEvent.click(await screen.findByRole("option", { name: "Bypass" }));

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ approvals: { default_level: "ask", max_level: "bypass" } }),
    );
  });
});
