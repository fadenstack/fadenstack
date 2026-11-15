/**
 * The definition editor: its "Chat sessions running at once" setting (shown, chosen, back to the default), the local
 * skills switch (off unless allowed), plain http on the local network (under Advanced, asked before it turns on),
 * and the approval levels (Bypass can be the highest level, never the one a conversation starts in).
 */
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

const optionNames = async (label: string) => {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  const names = within(await screen.findByRole("listbox"))
    .getAllByRole("option")
    .map((o) => o.textContent);
  await userEvent.keyboard("{Escape}");
  return names;
};

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

describe("AgentDefinitionEditor local skills", () => {
  it("shows them off by default, with what that means", async () => {
    render(<AgentDefinitionEditor value={EMPTY_DEFINITION} onChange={vi.fn()} />);
    expect(await screen.findByRole("combobox", { name: "Local skills" })).toHaveTextContent("Not allowed");
    expect(screen.getByText(/A person's own skills, kept on their device, stay off/)).toBeInTheDocument();
    expect(await optionNames("Local skills")).toEqual(["Not allowed", "Allowed"]);
  });

  it("saves Allowed as the version's local_skills_mode, and leaves local MCP as it was", async () => {
    const onChange = vi.fn();
    render(<Harness initial={EMPTY_DEFINITION} onChange={onChange} />);

    await userEvent.click(await screen.findByRole("combobox", { name: "Local skills" }));
    await userEvent.click(await screen.findByRole("option", { name: "Allowed" }));

    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ local_skills_mode: "allow", local_mcp_mode: "disable" }),
    );
    expect(screen.getByText(/Nobody in your organisation has reviewed them/)).toBeInTheDocument();
  });
});

describe("AgentDefinitionEditor plain http on the local network", () => {
  const PLAIN_HTTP = "Allow plain http on the local network";

  it("sits under Advanced, off, with what it means", async () => {
    render(<AgentDefinitionEditor value={EMPTY_DEFINITION} onChange={vi.fn()} />);
    expect(screen.queryByRole("switch", { name: PLAIN_HTTP })).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "Advanced" }));
    expect(screen.getByRole("switch", { name: PLAIN_HTTP })).not.toBeChecked();
    expect(screen.getByText(/Anyone on the network can read and change/)).toBeInTheDocument();
    expect(screen.getByText(/Developer mode does not change this/)).toBeInTheDocument();
  });

  it("warns before it turns on, and Cancel keeps it off", async () => {
    const onChange = vi.fn();
    render(<Harness initial={EMPTY_DEFINITION} onChange={onChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "Advanced" }));

    await userEvent.click(screen.getByRole("switch", { name: PLAIN_HTTP }));
    const dialog = await screen.findByRole("dialog", { name: "Allow plain http on the local network?" });
    expect(within(dialog).getByText(/the tool calls, their arguments and their results/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Never use it with secrets/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
    // Found again once the dialog has gone (the page is hidden from assistive technology while it closes).
    expect(await screen.findByRole("switch", { name: PLAIN_HTTP })).not.toBeChecked();
  });

  it("saves Allow as the version's local_mcp_plain_http, leaves local MCP as it was, and turns off at once", async () => {
    const onChange = vi.fn();
    render(<Harness initial={EMPTY_DEFINITION} onChange={onChange} />);
    await userEvent.click(await screen.findByRole("button", { name: "Advanced" }));

    await userEvent.click(screen.getByRole("switch", { name: PLAIN_HTTP }));
    const dialog = await screen.findByRole("dialog", { name: "Allow plain http on the local network?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Allow" }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ local_mcp_plain_http: "allow", local_mcp_mode: "disable" }),
    );
    expect(await screen.findByRole("switch", { name: PLAIN_HTTP })).toBeChecked();

    await userEvent.click(screen.getByRole("switch", { name: PLAIN_HTTP }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ local_mcp_plain_http: "deny" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("is never hidden while it is on", async () => {
    render(<AgentDefinitionEditor value={{ ...EMPTY_DEFINITION, local_mcp_plain_http: "allow" }} onChange={vi.fn()} readOnly />);
    expect(await screen.findByRole("switch", { name: PLAIN_HTTP })).toBeChecked();
    expect(screen.getByRole("switch", { name: PLAIN_HTTP })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Advanced" })).toBeDisabled();
  });
});

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
