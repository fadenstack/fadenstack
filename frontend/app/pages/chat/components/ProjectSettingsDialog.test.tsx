/**
 * The project settings: its default model, and its files.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import type { ChatProject } from "~/api/chatTypes";
import ProjectSettingsDialog from "./ProjectSettingsDialog";

function project(model_alias: string | null): ChatProject {
  return {
    id: "p1",
    tenant_id: "t",
    user_id: "u",
    name: "Roadmap",
    description: null,
    system_instructions: null,
    model_alias,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
  };
}

beforeEach(() => {
  vi.spyOn(chatApi, "listProjectAttachments").mockResolvedValue([]);
  vi.spyOn(chatApi, "listModels").mockResolvedValue([
    { alias: "qwen-chat", description: null, enabled: true },
    { alias: "bge-embed", description: null, enabled: true, kind: "embeddings" },
    { alias: "llama-chat", description: null, enabled: true, kind: "chat" },
  ]);
});

function open(model: string | null, onSave = vi.fn().mockResolvedValue(undefined)) {
  render(<ProjectSettingsDialog open project={project(model)} onClose={() => {}} onSave={onSave} />);
  return onSave;
}

const picker = () => screen.getByRole("combobox", { name: "Default model" });

describe("the project's default model", () => {
  it("offers the chat models and None, and not an embedding model", async () => {
    open(null);

    fireEvent.mouseDown(picker());
    const list = await screen.findByRole("listbox");
    await waitFor(() => expect(within(list).getByRole("option", { name: "qwen-chat" })).toBeInTheDocument());
    expect(within(list).getByRole("option", { name: "None (the chat's own choice)" })).toBeInTheDocument();
    expect(within(list).getByRole("option", { name: "llama-chat" })).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: "bge-embed" })).toBeNull();
  });

  it("saves the picked model", async () => {
    const onSave = open(null);

    fireEvent.mouseDown(picker());
    fireEvent.click(await screen.findByRole("option", { name: "llama-chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][1]).toMatchObject({ model_alias: "llama-chat" });
  });

  it("clears it with None (null, so the gateway takes it away)", async () => {
    const onSave = open("qwen-chat");
    await waitFor(() => expect(picker()).toHaveTextContent("qwen-chat"));

    fireEvent.mouseDown(picker());
    fireEvent.click(await screen.findByRole("option", { name: "None (the chat's own choice)" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][1]).toMatchObject({ model_alias: null });
  });

  it("sends no model when it did not change", async () => {
    const onSave = open("qwen-chat");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][1]).not.toHaveProperty("model_alias");
  });
});

describe("the project's files in its settings", () => {
  it("has a Files section", async () => {
    open(null);

    expect(await screen.findByRole("region", { name: "Files" })).toBeInTheDocument();
    expect(chatApi.listProjectAttachments).toHaveBeenCalledWith("p1");
  });
});
