/**
 * Settings has a "Usage and privacy" tab, and the usage settings live only
 * there (with their ranges and what they mean), not among the general ones.
 */
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as settingsApi from "~/api/systemSettings";
import type { SystemSettingSchemaItem } from "~/api/systemSettings";
import * as api from "~/api/usage";
import { renderPage } from "~/test/renderPage";
import { usageLimits, usageSettings } from "~/test/usageFixtures";

import SettingsPage from "./SettingsPage";

vi.mock("~/api/systemSettings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/systemSettings")>();
  return { ...actual, systemSettingsApi: { ...actual.systemSettingsApi, schema: vi.fn(), values: vi.fn() } };
});

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return { ...actual, usageApi: { ...actual.usageApi, settings: vi.fn(), limits: vi.fn() } };
});

// Panels of their own, with their own calls; not what this test is about.
vi.mock("~/components/HuggingFaceAccess", () => ({ HuggingFaceTokenPanel: () => null }));
vi.mock("~/components/ModulesTab", () => ({ default: () => null }));

function item(key: string, category: string, label: string): SystemSettingSchemaItem {
  return {
    key,
    type: "int",
    category,
    group: category,
    label,
    description: "",
    is_secret: false,
    default: 5,
    apply_scope: "live_reload",
    service_targets: [],
    protected: false,
    enum_values: [],
  };
}

beforeEach(() => {
  vi.mocked(settingsApi.systemSettingsApi.schema).mockResolvedValue([
    item("api.log_level_days", "logging", "Log days"),
    item("usage.min_group_size", "usage", "Minimum group size from the registry"),
  ]);
  vi.mocked(settingsApi.systemSettingsApi.values).mockResolvedValue({ items: {} });
  vi.mocked(api.usageApi.settings).mockResolvedValue(usageSettings());
  vi.mocked(api.usageApi.limits).mockResolvedValue(usageLimits());
});

describe("Settings > Usage and privacy", () => {
  it("keeps the usage settings out of the general tab", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=general" });

    expect(await screen.findByLabelText("Log days")).toBeInTheDocument();
    expect(screen.queryByLabelText("Minimum group size from the registry")).toBeNull();
    expect(screen.getByRole("tab", { name: "Usage and privacy" })).toBeInTheDocument();
  });

  it("opens the usage tab from its address", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=usage" });

    expect(await screen.findByText("Privacy of usage figures")).toBeInTheDocument();
    expect(screen.getByText("Limits of the instance")).toBeInTheDocument();
    expect(screen.getByText("Configuration statement")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Usage and privacy" })).toHaveAttribute("aria-selected", "true");
  });
});
