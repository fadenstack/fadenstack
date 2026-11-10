/**
 * A protected setting says that changing it needs root mode; one that can be narrowed without it says that only
 * turning it on does.
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

vi.mock("~/components/HuggingFaceAccess", () => ({ HuggingFaceTokenPanel: () => null }));
vi.mock("~/components/ModulesTab", () => ({ default: () => null }));

function item(key: string, label: string, extra: Partial<SystemSettingSchemaItem>): SystemSettingSchemaItem {
  return {
    key,
    type: "int",
    category: "approvals",
    group: "evidence",
    label,
    description: "",
    is_secret: false,
    default: 0,
    apply_scope: "live_reload",
    service_targets: [],
    protected: true,
    enum_values: [],
    ...extra,
  };
}

beforeEach(() => {
  vi.mocked(settingsApi.systemSettingsApi.schema).mockResolvedValue([
    item("approvals.result_summary_chars", "Result summary length", { default: 200, narrowing_values: [0] }),
    item("approvals.plain_protected", "Plain protected", {}),
  ]);
  vi.mocked(settingsApi.systemSettingsApi.values).mockResolvedValue({ items: {} });
  vi.mocked(api.usageApi.settings).mockResolvedValue(usageSettings());
  vi.mocked(api.usageApi.limits).mockResolvedValue(usageLimits());
});

describe("Settings > protected settings", () => {
  it("names root mode only for turning on when the setting can be narrowed without it", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=approvals" });

    expect(await screen.findByLabelText("Result summary length")).toBeInTheDocument();
    expect(screen.getByText("Turning it on needs Root Mode; turning it off does not.")).toBeInTheDocument();
    // One without narrowing values (an older server sends none) keeps the plain note.
    expect(screen.getByText("Changing it needs root mode: switch it on in the top bar.")).toBeInTheDocument();
    expect(screen.getByText(/Evidence/)).toBeInTheDocument();
  });
});
