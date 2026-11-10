/**
 * Settings → Approvals: the approval settings on a tab of their own, as Help names it. They were sections of
 * General ("Approvals / Bypass", "/ Grants", "/ Retention", "/ Evidence"), and Help sent administrators to a tab
 * that was not there (0.7.0rc1 on a test server, 10 November 2026). Allow Bypass says once that turning it on
 * needs Root Mode: its description said it as well as the note the console adds.
 */
import { fireEvent, screen } from "@testing-library/react";
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

function item(key: string, group: string, label: string, extra: Partial<SystemSettingSchemaItem> = {}): SystemSettingSchemaItem {
  return {
    key,
    type: "int",
    category: key.startsWith("approvals.") ? "approvals" : "auth",
    group,
    label,
    description: `${label} description.`,
    is_secret: false,
    default: 0,
    apply_scope: "live_reload",
    service_targets: [],
    protected: false,
    enum_values: [],
    ...extra,
  };
}

beforeEach(() => {
  vi.mocked(settingsApi.systemSettingsApi.schema).mockResolvedValue([
    item("auth.jwt.lifetime", "jwt", "Token lifetime"),
    item("approvals.bypass_allowed", "bypass", "Allow Bypass", {
      type: "bool",
      default: false,
      protected: true,
      narrowing_values: [false],
      // The registry's description, without the Root Mode sentence the console adds (issue 12).
      description: "Off: nobody can choose Bypass, and running Bypass sessions end within half a minute.",
    }),
    item("approvals.longest_grant_minutes", "grants", "Longest grant (minutes)"),
    item("approvals.bypass_retention_days", "retention", "Keep Bypass records (days)"),
    item("approvals.result_summary_chars", "evidence", "Result summary (characters)"),
  ]);
  vi.mocked(settingsApi.systemSettingsApi.values).mockResolvedValue({ items: {} });
  vi.mocked(api.usageApi.settings).mockResolvedValue(usageSettings());
  vi.mocked(api.usageApi.limits).mockResolvedValue(usageLimits());
});

describe("Settings → Approvals", () => {
  it("holds the approval settings, by group, and General no longer does", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=general" });

    expect(await screen.findByLabelText("Token lifetime")).toBeInTheDocument();
    expect(screen.queryByText("Allow Bypass")).not.toBeInTheDocument();
    expect(screen.queryByText(/Approvals \//)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Approvals" }));

    expect(await screen.findByText("Allow Bypass")).toBeInTheDocument();
    for (const label of ["Longest grant (minutes)", "Keep Bypass records (days)", "Result summary (characters)"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    for (const heading of ["Bypass", "Grants", "Retention", "Evidence"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
    expect(screen.queryByLabelText("Token lifetime")).not.toBeInTheDocument();
  });

  it("opens from its address, as Help's links do", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=approvals" });

    expect(await screen.findByText("Allow Bypass")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals", selected: true })).toBeInTheDocument();
  });

  it("says once that turning Allow Bypass on needs Root Mode", async () => {
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=approvals" });

    expect(await screen.findByText("Allow Bypass")).toBeInTheDocument();
    expect(screen.getAllByText(/Turning it on needs Root Mode/)).toHaveLength(1);
  });
});
