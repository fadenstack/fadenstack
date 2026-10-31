/**
 * Settings > Usage and privacy: the ranges the backend enforces are checked
 * before anything is sent (M04, M09), keeping per-person figures past 90 days
 * is warned about (S06), the person-level switch is locked without root mode
 * and its refusal worded, and the limits and the statement work (S09, S11).
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "~/api/http";
import * as api from "~/api/usage";
import { UsageProblemsError } from "~/api/usage";
import { renderPage } from "~/test/renderPage";
import { usageLimits, usageSettings } from "~/test/usageFixtures";

import UsagePrivacySettings from "./UsagePrivacySettings";

vi.mock("~/api/usage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/usage")>();
  return {
    ...actual,
    usageApi: {
      ...actual.usageApi,
      settings: vi.fn(),
      updateSettings: vi.fn(),
      limits: vi.fn(),
      updateLimits: vi.fn(),
    },
  };
});

const usage = vi.mocked(api.usageApi);

function view(rootModeActive = false) {
  return renderPage(<UsagePrivacySettings />, { path: "/admin/settings", context: { rootModeActive } });
}

function field(container: HTMLElement, key: string): HTMLInputElement {
  return container.querySelector(`[data-usage-setting='${key}']`) as HTMLInputElement;
}

function limitField(container: HTMLElement, key: string): HTMLInputElement {
  return container.querySelector(`[data-usage-limit='${key}']`) as HTMLInputElement;
}

async function privacy() {
  return (await screen.findByText("Privacy of usage figures")).closest(".MuiPaper-root") as HTMLElement;
}

beforeEach(() => {
  usage.settings.mockReset().mockResolvedValue(usageSettings());
  usage.updateSettings.mockReset();
  usage.limits.mockReset().mockResolvedValue(usageLimits());
  usage.updateLimits.mockReset();
});

describe("usage privacy settings", () => {
  it("keeps k and the retention periods in their ranges before sending", async () => {
    const { container } = view();
    const section = await privacy();
    const save = within(section).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // nothing changed yet

    const k = field(container, "min_group_size");
    await userEvent.clear(k);
    await userEvent.type(k, "4");
    expect(within(section).getByText("A whole number from 5 to 50.")).toBeInTheDocument();
    expect(save).toBeDisabled();

    await userEvent.clear(k);
    await userEvent.type(k, "51");
    expect(save).toBeDisabled();

    const log = field(container, "request_log_retention_days");
    await userEvent.clear(log);
    await userEvent.type(log, "400");
    expect(within(section).getByText("A whole number from 7 to 365.")).toBeInTheDocument();
    await userEvent.clear(log);
    await userEvent.type(log, "90");

    await userEvent.clear(k);
    await userEvent.type(k, "10");
    expect(save).toBeEnabled();

    usage.updateSettings.mockResolvedValue(usageSettings({ min_group_size: 10 }));
    await userEvent.click(save);
    await waitFor(() => expect(usage.updateSettings).toHaveBeenCalledWith({ min_group_size: 10 }));
    expect(await within(section).findByText("Saved.")).toBeInTheDocument();
  });

  it("warns when per-person figures are kept longer than 90 days", async () => {
    const { container } = view();
    await privacy();

    const days = field(container, "person_retention_days");
    await userEvent.clear(days);
    await userEvent.type(days, "120");

    expect(screen.getByText("Longer than 90 days needs a documented reason.")).toBeInTheDocument();
  });

  it("explains person-level reporting and the works council, and locks it without root mode", async () => {
    const { container } = view(false);
    const section = await privacy();

    const toggle = within(section).getByRole("switch", { name: "Person-level reporting" });
    expect(toggle).toBeDisabled();
    expect(container.querySelector("[data-usage-root-hint]")?.textContent).toBe("Turn on root mode to change this switch.");
    expect(within(section).getByText(/Off \(the default\): nobody sees another person's figures/)).toBeInTheDocument();
    expect(within(section).getByText(/Where there is a works council, agree it with them first/)).toBeInTheDocument();
  });

  it("words the server's root mode refusal", async () => {
    usage.updateSettings.mockRejectedValue(new ApiError("forbidden", 403, "root_mode"));
    view(true);
    const section = await privacy();

    const toggle = within(section).getByRole("switch", { name: "Person-level reporting" });
    expect(toggle).toBeEnabled();
    await userEvent.click(toggle);
    expect(within(section).getByText(/usage auditor role, which nobody holds at installation/)).toBeInTheDocument();
    await userEvent.click(within(section).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(usage.updateSettings).toHaveBeenCalledWith({ person_reporting: true }));
    expect(
      await within(section).findByText("Turning person-level reporting on or off needs root mode. Turn on root mode and try again."),
    ).toBeInTheDocument();
  });

  it("shows the fields the server rejected", async () => {
    usage.updateSettings.mockRejectedValue(new UsageProblemsError([{ field: "time_zone", code: "invalid" }]));
    view();
    const section = await privacy();

    const zone = within(section).getByRole("textbox", { name: "Time zone" });
    await userEvent.clear(zone);
    await userEvent.type(zone, "Europe/Paris");
    await userEvent.click(within(section).getByRole("button", { name: "Save" }));

    expect(await within(section).findByText("Not a valid value.")).toBeInTheDocument();
  });

  it("takes an https address for the privacy notice, or none", async () => {
    usage.updateSettings.mockResolvedValue(usageSettings({ privacy_notice_url: "https://intranet.example.com/privacy" }));
    view();
    const section = await privacy();
    const notice = within(section).getByRole("textbox", { name: "Privacy notice" });
    const save = within(section).getByRole("button", { name: "Save" });

    await userEvent.type(notice, "http://intranet.example.com/privacy");
    expect(within(section).getByText("An https address, or empty.")).toBeInTheDocument();
    expect(save).toBeDisabled();

    await userEvent.clear(notice);
    await userEvent.type(notice, "https://intranet.example.com/privacy");
    await userEvent.click(save);
    await waitFor(() =>
      expect(usage.updateSettings).toHaveBeenCalledWith({ privacy_notice_url: "https://intranet.example.com/privacy" }),
    );
  });

  it("refuses a time zone it does not know", async () => {
    view();
    const section = await privacy();

    const zone = within(section).getByRole("textbox", { name: "Time zone" });
    await userEvent.clear(zone);
    await userEvent.type(zone, "Mars/Olympus");

    expect(within(section).getByText("Not a known time zone.")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("instance limits", () => {
  it("saves whole numbers, empty for no limit", async () => {
    usage.updateLimits.mockResolvedValue(usageLimits({ rpm_limit: 600 }));
    const { container } = view();
    const section = (await screen.findByText("Limits of the instance")).closest(".MuiPaper-root") as HTMLElement;
    const save = within(section).getByRole("button", { name: "Save" });

    await userEvent.type(limitField(container, "rpm_limit"), "0");
    expect(within(section).getByText("A whole number above 0, or empty.")).toBeInTheDocument();
    expect(save).toBeDisabled();

    await userEvent.clear(limitField(container, "rpm_limit"));
    await userEvent.type(limitField(container, "rpm_limit"), "600");
    await userEvent.click(save);

    await waitFor(() =>
      expect(usage.updateLimits).toHaveBeenCalledWith({ rpm_limit: 600, tpm_limit: null, tokens_per_day: null }),
    );
  });
});

describe("links to Help", () => {
  it("links each section to the Help section that explains it", async () => {
    usage.settings.mockResolvedValue(usageSettings());
    view();
    await privacy();

    const hrefs = screen.getAllByRole("link", { name: "Help" }).map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/help/administer/usage-privacy/#the-settings",
      "/help/administer/usage/#limits-for-the-whole-instance",
      "/help/administer/usage-documents/",
    ]);
  });
});

describe("configuration statement", () => {
  it("downloads the statement in the chosen language", async () => {
    view();

    const link = await screen.findByRole("link", { name: "Download" });
    expect(link).toHaveAttribute("href", "/api/admin/usage/statement?language=en");

    await userEvent.click(screen.getByRole("combobox", { name: "Language" }));
    await userEvent.click(await screen.findByRole("option", { name: "Deutsch" }));
    expect(link).toHaveAttribute("href", "/api/admin/usage/statement?language=de");
  });
});
