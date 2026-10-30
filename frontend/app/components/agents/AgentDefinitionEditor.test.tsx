/** The definition editor's "Chat sessions running at once" setting: shown, chosen, back to the default. */
import { useState } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentDefinition } from "~/api/agents";

import { EMPTY_DEFINITION } from "./agentDefinition";
import { AgentDefinitionEditor } from "./AgentDefinitionEditor";

vi.mock("~/api/chatClient", () => ({ chatApi: { listModels: vi.fn().mockResolvedValue([]) } }));
vi.mock("~/api/about", () => ({ hasFeature: vi.fn().mockResolvedValue(false) }));
vi.mock("~/api/rag", () => ({ ragLite: { listCollections: vi.fn().mockResolvedValue([]) } }));

function Harness({ initial, onChange }: { initial: AgentDefinition; onChange: (d: AgentDefinition) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <AgentDefinitionEditor
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

describe("AgentDefinitionEditor sessions setting", () => {
  it("shows the default and the hint when the key is absent", async () => {
    render(<Harness initial={EMPTY_DEFINITION} onChange={() => {}} />);
    const field = await screen.findByRole("combobox", { name: "Chat sessions running at once" });
    expect(field).toHaveTextContent("Default (5)");
    expect(screen.getByText(/How many chat sessions of this agent can work at the same time/)).toBeInTheDocument();
  });

  it("shows the stored value", async () => {
    render(<Harness initial={{ ...EMPTY_DEFINITION, ui: { max_running_sessions: 3 } }} onChange={() => {}} />);
    expect(await screen.findByRole("combobox", { name: "Chat sessions running at once" })).toHaveTextContent("3");
  });

  it("offers 1 to 5, sets the key, and the default removes it", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness initial={{ ...EMPTY_DEFINITION, ui: { greeting: "Hi" } }} onChange={onChange} />);

    await user.click(await screen.findByRole("combobox", { name: "Chat sessions running at once" }));
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Default (5)", "1", "2", "3", "4", "5"]);
    await user.click(options[2]);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ ui: { greeting: "Hi", max_running_sessions: 2 } }));

    await user.click(screen.getByRole("combobox", { name: "Chat sessions running at once" }));
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Default (5)" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ ui: { greeting: "Hi" } }));
  });
});
