/** The Mac's status and its memory plan, as the console shows them. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Fit } from "~/api/marketplace";

import { MacFitPanel } from "./MacFitPanel";
import { MacStatusCard } from "./MacStatusCard";

const GIB = 2 ** 30;

describe("MacStatusCard", () => {
  it("warns about a firewall that is on and a sandbox that is off", () => {
    render(
      <MacStatusCard
        capabilities={{ native_sandbox: false, mac_status: { filevault: true, firewall: { enabled: true } } }}
      />,
    );
    expect(screen.getByTestId("mac-sandbox")).toHaveTextContent("Sandbox off");
    expect(screen.getByTestId("mac-firewall")).toHaveTextContent("application firewall is on");
    expect(screen.getByTestId("mac-filevault")).toHaveTextContent("fdesetup authrestart");
  });

  it("says unknown instead of guessing for an agent that reports nothing", () => {
    render(<MacStatusCard capabilities={{}} />);
    expect(screen.getByTestId("mac-sandbox")).toHaveTextContent("not reported");
    expect(screen.getByTestId("mac-firewall")).toHaveTextContent("not known");
    expect(screen.getByTestId("mac-runtime")).toHaveTextContent("not installed");
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
    expect(screen.getByTestId("mac-fit-too-large")).toHaveTextContent("does not fit this Mac");
  });

  it("shows nothing for a cluster that is not a Mac", () => {
    const { container } = render(<MacFitPanel fit={fit({ metal: null })} />);
    expect(container).toBeEmptyDOMElement();
  });
});
