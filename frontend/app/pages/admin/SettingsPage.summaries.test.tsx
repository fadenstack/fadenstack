/**
 * Settings > Chat > Summaries of long chats: one switch per chat model instead of a raw JSON list;
 * saving writes the list of the names that are on, and a name that is no longer a model stays.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";
import * as settingsApi from "~/api/systemSettings";
import type { SystemSettingSchemaItem } from "~/api/systemSettings";
import * as api from "~/api/usage";
import { renderPage } from "~/test/renderPage";
import { usageLimits, usageSettings } from "~/test/usageFixtures";

import SettingsPage from "./SettingsPage";

vi.mock("~/api/systemSettings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/systemSettings")>();
  return {
    ...actual,
    systemSettingsApi: { ...actual.systemSettingsApi, schema: vi.fn(), values: vi.fn(), update: vi.fn() },
  };
});

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return { ...actual, usageApi: { ...actual.usageApi, settings: vi.fn(), limits: vi.fn() } };
});

vi.mock("~/components/HuggingFaceAccess", () => ({ HuggingFaceTokenPanel: () => null }));
vi.mock("~/components/ModulesTab", () => ({ default: () => null }));

const REGISTRY_TEXT = "The chat models that write a summary of a long chat's earlier messages.";

const setting: SystemSettingSchemaItem = {
  key: "chat.summary_models",
  type: "json",
  category: "chat",
  group: "summaries",
  label: "Summaries of long chats",
  description: REGISTRY_TEXT,
  is_secret: false,
  default: [],
  apply_scope: "live_reload",
  service_targets: ["faden-gateway"],
  protected: false,
  enum_values: [],
};

function model(alias: string, kind: string | null = "chat") {
  return { alias, description: null, enabled: true, kind };
}

async function field() {
  return within(await screen.findByTestId("summary-models"));
}

beforeEach(() => {
  vi.mocked(settingsApi.systemSettingsApi.schema).mockResolvedValue([setting]);
  vi.mocked(settingsApi.systemSettingsApi.values).mockResolvedValue({
    items: { "chat.summary_models": { value: ["qwen-a", "gone-model"], masked: null } },
  } as never);
  vi.mocked(settingsApi.systemSettingsApi.update).mockResolvedValue({ apply_status: "applied", apply_scope: "live_reload" } as never);
  vi.mocked(api.usageApi.settings).mockResolvedValue(usageSettings());
  vi.mocked(api.usageApi.limits).mockResolvedValue(usageLimits());
  vi.spyOn(chatApi, "listModels").mockResolvedValue([
    model("qwen-a"),
    model("llama-b"),
    model("embed-c", "embeddings"),
  ]);
});

describe("Settings > Summaries of long chats", () => {
  it("shows one switch per chat model, on where the name is in the list, and keeps the registry's text", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=general" });
    const view = await field();

    await waitFor(() => expect(view.getByRole("switch", { name: "llama-b" })).toBeInTheDocument());
    expect(view.getByRole("switch", { name: "qwen-a" })).toBeChecked();
    expect(view.getByRole("switch", { name: "llama-b" })).not.toBeChecked();
    expect(view.queryByRole("switch", { name: "embed-c" })).toBeNull();
    expect(view.getByText(REGISTRY_TEXT)).toBeInTheDocument();
    expect(
      view.getByText("Off by default. A model writes the summary of a long chat's older messages; try it on your own chats first."),
    ).toBeInTheDocument();
    // No raw JSON field.
    expect(screen.queryByRole("textbox", { name: "Summaries of long chats" })).toBeNull();
  });

  it("shows a name that is no longer a model as a switch marked not found, and keeps it", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=general" });
    const view = await field();
    await waitFor(() => expect(view.getByRole("switch", { name: /gone-model/ })).toBeChecked());
    expect(view.getByText("not found")).toBeInTheDocument();

    fireEvent.click(view.getByRole("switch", { name: "llama-b" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(settingsApi.systemSettingsApi.update).toHaveBeenCalledWith(
        "chat.summary_models",
        ["qwen-a", "gone-model", "llama-b"],
        "local",
      ),
    );
  });

  it("writes the list without a model that is switched off", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=general" });
    const view = await field();
    await waitFor(() => expect(view.getByRole("switch", { name: "qwen-a" })).toBeChecked());

    fireEvent.click(view.getByRole("switch", { name: "qwen-a" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(settingsApi.systemSettingsApi.update).toHaveBeenCalledWith("chat.summary_models", ["gone-model"], "local"),
    );
  });
});
