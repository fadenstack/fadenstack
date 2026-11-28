/** The Mac's status and its memory plan, as the console shows them. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { Fit } from "~/api/marketplace";

import { MacFitPanel } from "./MacFitPanel";
import { MacServeSection } from "./MacServeSection";
import { MacStatusCard } from "./MacStatusCard";

const GIB = 2 ** 30;

describe("MacStatusCard", () => {
  it("shows one row of chips and spells out only what needs action", () => {
    render(
      <MacStatusCard
        capabilities={{
          native_sandbox: false,
          metal: { recommended_max_working_set_bytes: 52 * GIB },
          mac_status: { filevault: true, firewall: { enabled: true } },
        }}
      />,
    );
    expect(screen.getByTestId("mac-runtime")).toHaveTextContent("Runtime ready");
    expect(screen.getByTestId("mac-sandbox")).toHaveAttribute("data-tone", "warn");
    expect(screen.getByTestId("mac-firewall")).toHaveAttribute("data-tone", "warn");
    expect(screen.getByTestId("mac-memory")).toHaveTextContent("GPU memory");
    // Sandbox off and firewall on are warnings that stay visible; FileVault is only a chip.
    expect(screen.getByTestId("mac-sandbox-warning")).toHaveTextContent("Sandbox off");
    expect(screen.getByTestId("mac-firewall-warning")).toHaveTextContent("application firewall is on");
    expect(screen.queryByTestId("mac-filevault-warning")).not.toBeInTheDocument();
  });

  it("marks the Mac experimental", () => {
    render(<MacStatusCard capabilities={{}} />);
    expect(screen.getByTestId("experimental-chip")).toHaveTextContent("Experimental");
  });

  it("keeps the full sentences under Details", async () => {
    render(<MacStatusCard capabilities={{ native_sandbox: true, mac_status: { filevault: true, firewall: { enabled: false } } }} />);
    expect(screen.queryByTestId("mac-details")).not.toBeInTheDocument();
    expect(screen.queryByTestId("mac-sandbox-warning")).not.toBeInTheDocument();
    const toggle = screen.getByTestId("mac-details-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("mac-details")).toHaveTextContent("fdesetup authrestart");
  });

  it("says unknown instead of guessing for an agent that reports nothing", () => {
    render(<MacStatusCard capabilities={{}} />);
    expect(screen.getByTestId("mac-sandbox")).toHaveTextContent("Sandbox unknown");
    expect(screen.getByTestId("mac-firewall")).toHaveTextContent("Firewall unknown");
    expect(screen.getByTestId("mac-runtime")).toHaveTextContent("No runtime yet");
    expect(screen.queryByTestId("mac-memory")).not.toBeInTheDocument();
  });
});

function fit(over: Partial<Fit>): Fit {
  return {
    status: "fits", gpus_per_copy: 1, tensor_parallel: 1, copies: 1, copies_now: 1, context: 32768, max_context: 40960,
    needed_bytes_per_gpu: 30 * GIB, gpu_bytes: 52 * GIB, weights_bytes: 1.2 * GIB, kv_bytes_per_token: 114688,
    suggested_gpu_memory_utilization: 0.7, shareable: false, notes: [], max_num_seqs: 8,
    metal: { working_set_bytes: 52 * GIB, estimated: true, reserve_bytes: 4 * GIB, ceiling_bytes: 47 * GIB, budget_bytes: 33 * GIB, kv_bytes: 28 * GIB },
    ...over,
  };
}

describe("MacFitPanel", () => {
  it("shows the numbers and marks an estimate", () => {
    render(<MacFitPanel fit={fit({})} />);
    const panel = screen.getByTestId("mac-fit");
    expect(panel).toHaveTextContent("(estimated)");
    expect(panel).toHaveTextContent("70 %");
    expect(panel).toHaveTextContent("32,768");
  });

  it("refuses a model that does not fit, with the figures", () => {
    render(<MacFitPanel fit={fit({ status: "too_large", needed_bytes_per_gpu: 90 * GIB })} />);
    expect(screen.getByTestId("mac-fit-too-large")).toHaveTextContent("does not fit the Mac");
  });

  it("shows nothing for a cluster that is not a Mac", () => {
    const { container } = render(<MacFitPanel fit={fit({ metal: null })} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("MacServeSection", () => {
  it("offers the button only to a Mac whose proxy is native", () => {
    const { rerender } = render(
      <MemoryRouter><MacServeSection nodeId="n" capabilities={{ native_runtime: 1 }} /></MemoryRouter>,
    );
    expect(screen.queryByTestId("mac-serve-button")).not.toBeInTheDocument();
    rerender(
      <MemoryRouter><MacServeSection nodeId="n" capabilities={{ native_runtime: 1, model_tls_native: 1 }} /></MemoryRouter>,
    );
    expect(screen.getByTestId("mac-serve-button")).toBeInTheDocument();
  });
});

describe("locales", () => {
  it("has the new Mac status keys in every language", async () => {
    for (const l of ["en", "de", "fr", "es", "zh"]) {
      const { default: common } = await import(`../../../../backend/i18n/${l}/common.json`);
      for (const k of ["chip_runtime_ready", "chip_sandbox_off", "chip_firewall_on", "chip_filevault_on", "chip_memory", "memory_help", "details_show", "details_hide"]) {
        expect((common.mac as Record<string, string>)[k], `${l} ${k}`).toBeTruthy();
      }
    }
  });
});
