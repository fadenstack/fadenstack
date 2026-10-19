/**
 * What the provenance engine says about itself, as the Modules page shows it:
 * every state the backend reports is a sentence, never an error page.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as provenance from "~/api/provenance";

import ProvenanceStatus from "./ProvenanceStatus";

vi.mock("~/api/provenance", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/provenance")>()),
  fetchProvenanceStatus: vi.fn(),
}));

const status = vi.mocked(provenance.fetchProvenanceStatus);

const RUNNING: provenance.ProvenanceStatus = {
  enabled: true,
  state: "running",
  contract: "provenance/v1",
  expected_contract: "provenance/v1",
  contract_supported: true,
  engine_version: "0.5.0",
  capabilities: { images: true, watermark_audio: false },
  plugins: ["acme", "hsm"],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ProvenanceStatus", () => {
  it("lists the capabilities, naming the ones it does not know by their id", async () => {
    status.mockResolvedValue(RUNNING);
    render(<ProvenanceStatus />);

    expect(await screen.findByTestId("provenance-running")).toBeInTheDocument();
    expect(screen.getByTestId("provenance-capability-images")).toHaveTextContent(
      "Pictures: available",
    );
    expect(screen.getByTestId("provenance-capability-images")).toHaveAttribute(
      "data-available",
      "true",
    );
    expect(screen.getByTestId("provenance-capability-watermark_audio")).toHaveTextContent(
      "watermark_audio: not available yet",
    );
    expect(screen.getByTestId("provenance-plugins")).toHaveTextContent("Plugins: acme, hsm");
    expect(screen.queryByTestId("provenance-contract-unsupported")).not.toBeInTheDocument();
  });

  it("warns when the engine speaks another contract", async () => {
    status.mockResolvedValue({
      ...RUNNING,
      contract: "provenance/v2",
      contract_supported: false,
    });
    render(<ProvenanceStatus />);

    expect(await screen.findByTestId("provenance-contract-unsupported")).toHaveTextContent(
      "This server speaks provenance/v1, the engine provenance/v2.",
    );
  });

  it.each([
    ["refused", "The engine refused the service token", "the same service token"],
    ["unexpected", "The engine gave an unexpected answer", "the engine's address"],
    ["not_running", "Not running", "while the engine is not running"],
  ] as const)("says what a %s engine means", async (state, headline, hint) => {
    status.mockResolvedValue({
      ...RUNNING,
      state,
      contract: null,
      contract_supported: false,
      engine_version: null,
      capabilities: {},
      plugins: [],
    });
    render(<ProvenanceStatus />);

    const notice = await screen.findByTestId("provenance-not-running");
    expect(notice).toHaveTextContent(headline);
    expect(notice).toHaveTextContent(hint);
  });

  it("says so when the status itself cannot be read", async () => {
    status.mockRejectedValue(new Error("API 403: Permission denied: modules:read"));
    render(<ProvenanceStatus />);

    expect(await screen.findByTestId("provenance-status-error")).toHaveTextContent(
      "The module's status could not be read.",
    );
  });

  it("asks again when its refresh key changes", async () => {
    status.mockResolvedValue(RUNNING);
    const { rerender } = render(<ProvenanceStatus refreshKey={0} />);
    await screen.findByTestId("provenance-running");
    rerender(<ProvenanceStatus refreshKey={1} />);
    await vi.waitFor(() => expect(status).toHaveBeenCalledTimes(2));
  });
});
