/**
 * Picking skills in the chat input: ``$`` and a name, the chips, ``/skill``.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { SkillSummary } from "~/api/skills";
import { ChatSkillsProvider, type ChatSkills } from "../hooks/useChatSkills";
import ChatInput from "./ChatInput";

function skill(slug: string, name: string): SkillSummary {
  return {
    id: slug,
    tenant_id: "default",
    name,
    slug,
    description: `${name} description`,
    owner_id: "u",
    visibility: "private",
    activation: "manual",
    status: "published",
    enabled: true,
    priority: 50,
    tags: [],
    latest_version: 1,
    published_version: 1,
    created_by: "u",
    created_at: "",
    updated_at: "",
    access: "owner",
  };
}

function skillsWith(overrides: Partial<ChatSkills> = {}): ChatSkills {
  return {
    enabled: true,
    canCreate: true,
    usable: [skill("code-review", "Code Review"), skill("tone", "Tone of Voice")],
    selected: [],
    toggle: vi.fn(),
    remove: vi.fn(),
    openCreate: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  };
}

function renderInput(skills: ChatSkills | null, onSend = vi.fn()) {
  const input = (
    <ChatInput onSend={onSend} selectedModel="m" onModelChange={() => {}} autoFocus={false} />
  );
  render(
    <MemoryRouter>
      {skills ? <ChatSkillsProvider value={skills}>{input}</ChatSkillsProvider> : input}
    </MemoryRouter>,
  );
  return screen.getByRole("textbox") as HTMLTextAreaElement;
}

function type(box: HTMLTextAreaElement, value: string) {
  fireEvent.change(box, { target: { value } });
}

describe("ChatInput skills", () => {
  beforeEach(() => {
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("offers the skills whose name matches what follows $, and picks one", () => {
    const skills = skillsWith();
    const box = renderInput(skills);
    type(box, "please $co");
    const picker = screen.getByRole("menu", { name: "Skills" });
    expect(within(picker).getByText(/\$code-review/)).toBeInTheDocument();
    expect(within(picker).queryByText(/\$tone/)).toBeNull();

    fireEvent.keyDown(box, { key: "Enter" });
    expect(skills.toggle).toHaveBeenCalledWith("code-review");
    expect(box.value).toBe("please ");
  });

  it("does not send while the picker is open", () => {
    const onSend = vi.fn();
    const box = renderInput(skillsWith(), onSend);
    type(box, "$to");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows the picked skills and takes one off", () => {
    const skills = skillsWith({ selected: ["tone"] });
    renderInput(skills);
    const chip = screen.getByText("Tone of Voice").closest(".MuiChip-root") as HTMLElement;
    fireEvent.click(within(chip).getByTestId("CancelIcon"));
    expect(skills.remove).toHaveBeenCalledWith("tone");
  });

  it("opens the create dialog on /skill instead of sending it", () => {
    const onSend = vi.fn();
    const skills = skillsWith();
    const box = renderInput(skills, onSend);
    type(box, "/skill review like we just did");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(skills.openCreate).toHaveBeenCalledWith("review like we just did");
    expect(onSend).not.toHaveBeenCalled();
    expect(box.value).toBe("");
  });

  it("sends /skill as a message when skills are off", () => {
    const onSend = vi.fn();
    const box = renderInput(null, onSend);
    expect(screen.queryByRole("button", { name: "Skills" })).toBeNull();
    type(box, "/skill please");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("/skill please", []);
  });
});
