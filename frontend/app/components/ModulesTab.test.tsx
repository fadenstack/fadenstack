/**
 * Settings > Modules: the provenance module shows what its engine says, and
 * switching it off asks for an acknowledgement of what is lost (EU AI Act
 * Art. 50(2)) before the request goes out with `confirm: true`. Other modules
 * keep the plain confirmation.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as provenance from "~/api/provenance";
import { servicesApi, type ServiceInfo } from "~/api/services";
import { systemSettingsApi } from "~/api/systemSettings";
import { useServices } from "~/lib/ServicesContext";

import ModulesTab from "./ModulesTab";

vi.mock("~/lib/ServicesContext", () => ({ useServices: vi.fn() }));
vi.mock("~/api/provenance", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/provenance")>()),
  fetchProvenanceStatus: vi.fn(),
}));

const services = vi.mocked(useServices);
const status = vi.mocked(provenance.fetchProvenanceStatus);

const PROVENANCE: ServiceInfo = {
  name: "provenance",
  display_name: "Provenance",
  description: "Marks AI-generated content as such.",
  configured: true,
  enabled: true,
  status: "healthy",
  module_type: "container",
  containers: [{ name: "faden-provenance", state: "running" }],
  disable_warning: "Generated content will no longer be marked as AI-generated.",
};

const SKILLS: ServiceInfo = {
  name: "skills",
  display_name: "Skills Registry",
  description: "Reusable instructions.",
  configured: true,
  enabled: true,
  status: "healthy",
  module_type: "container",
  containers: [{ name: "faden-skills", state: "running" }],
  disable_warning: null,
};

const RUNNING: provenance.ProvenanceStatus = {
  enabled: true,
  state: "running",
  contract: "provenance/v1",
  expected_contract: "provenance/v1",
  contract_supported: true,
  engine_version: "0.5.0",
  capabilities: { images: false, receipts: true, records: false, detect: false },
  plugins: [],
};

const refresh = vi.fn(async () => {});

beforeEach(() => {
  vi.clearAllMocks();
  services.mockReturnValue({
    services: [PROVENANCE, SKILLS],
    loading: false,
    error: null,
    isModuleEnabled: () => true,
    getModule: (name: string) => [PROVENANCE, SKILLS].find((s) => s.name === name),
    refresh,
  });
  vi.spyOn(systemSettingsApi, "schema").mockResolvedValue([]);
  vi.spyOn(systemSettingsApi, "values").mockResolvedValue({ items: {} });
  status.mockResolvedValue(RUNNING);
});

function done(module: string, action: "enable" | "disable") {
  return { module, action, errors: [] as string[] };
}

describe("ModulesTab: provenance", () => {
  it("shows the module with the contract and capabilities its engine reports", async () => {
    render(<ModulesTab />);

    expect(screen.getByText("Provenance")).toBeInTheDocument();
    expect(screen.getByText(/EU AI Act, Art\. 50\(2\)/)).toBeInTheDocument();
    expect(await screen.findByTestId("provenance-contract")).toHaveTextContent(
      "Contract: provenance/v1",
    );
    expect(screen.getByText("Version 0.5.0")).toBeInTheDocument();
    expect(screen.getByTestId("provenance-capability-receipts")).toHaveTextContent(
      "Receipts: available",
    );
    expect(screen.getByTestId("provenance-capability-images")).toHaveTextContent(
      "Pictures: not available yet",
    );
    expect(screen.getByTestId("provenance-plugins")).toHaveTextContent("No plugins loaded");
  });

  it("shows an engine that does not answer as not running", async () => {
    status.mockResolvedValue({
      ...RUNNING,
      state: "not_running",
      contract: null,
      contract_supported: false,
      engine_version: null,
      capabilities: {},
    });
    render(<ModulesTab />);

    const notice = await screen.findByTestId("provenance-not-running");
    expect(notice).toHaveTextContent("Not running");
    expect(notice).toHaveTextContent("Content cannot be marked while the engine is not running.");
  });

  it("switches off only after the warning is acknowledged, and sends the confirmation", async () => {
    const disable = vi
      .spyOn(servicesApi, "disable")
      .mockResolvedValue(done("provenance", "disable"));
    render(<ModulesTab />);

    await userEvent.click(screen.getByRole("switch", { name: "Toggle Provenance" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("module-disable-warning")).toHaveTextContent(
      "EU AI Act (Art. 50(2))",
    );
    expect(dialog).toHaveTextContent("recorded in the audit log");

    const confirm = within(dialog).getByTestId("module-confirm-action");
    expect(confirm).toHaveTextContent("Switch off");
    expect(confirm).toBeDisabled();
    await userEvent.click(within(dialog).getByTestId("module-disable-ack"));
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() =>
      expect(disable).toHaveBeenCalledWith("provenance", { confirm: true }),
    );
    expect(refresh).toHaveBeenCalled();
  });

  it("asks again the next time: the acknowledgement is not kept", async () => {
    const disable = vi.spyOn(servicesApi, "disable");
    render(<ModulesTab />);

    await userEvent.click(screen.getByRole("switch", { name: "Toggle Provenance" }));
    let dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByTestId("module-disable-ack"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole("switch", { name: "Toggle Provenance" }));
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByTestId("module-confirm-action")).toBeDisabled();
    expect(disable).not.toHaveBeenCalled();
  });

  it("carries the public check page's settings in its card", async () => {
    render(<ModulesTab />);

    await userEvent.click(screen.getByRole("button", { name: "Module settings" }));
    expect(
      await screen.findByRole("switch", { name: "Offer a public check page" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Contact for text checks" })).toBeInTheDocument();
  });

  it("shows the server's refusal when it asks for the confirmation", async () => {
    vi.spyOn(servicesApi, "disable").mockRejectedValue(
      new Error("To switch it off, confirm it (send confirm: true)."),
    );
    render(<ModulesTab />);

    await userEvent.click(screen.getByRole("switch", { name: "Toggle Provenance" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByTestId("module-disable-ack"));
    await userEvent.click(within(dialog).getByTestId("module-confirm-action"));

    expect(await screen.findByText(/confirm it \(send confirm: true\)/)).toBeInTheDocument();
  });
});

describe("ModulesTab: other modules", () => {
  it("keep the plain confirmation, without the acknowledgement", async () => {
    const disable = vi
      .spyOn(servicesApi, "disable")
      .mockResolvedValue(done("skills", "disable"));
    render(<ModulesTab />);

    await userEvent.click(screen.getByRole("switch", { name: "Toggle Skills Registry" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByTestId("module-disable-warning")).not.toBeInTheDocument();
    expect(within(dialog).queryByTestId("module-disable-ack")).not.toBeInTheDocument();
    const confirm = within(dialog).getByTestId("module-confirm-action");
    expect(confirm).toHaveTextContent("Stop");
    await userEvent.click(confirm);

    await waitFor(() =>
      expect(disable).toHaveBeenCalledWith("skills", { confirm: false }),
    );
  });

  it("do not ask the provenance engine", async () => {
    services.mockReturnValue({
      services: [SKILLS],
      loading: false,
      error: null,
      isModuleEnabled: () => true,
      getModule: () => SKILLS,
      refresh,
    });
    render(<ModulesTab />);

    expect(await screen.findByText("Skills Registry")).toBeInTheDocument();
    expect(status).not.toHaveBeenCalled();
  });
});
