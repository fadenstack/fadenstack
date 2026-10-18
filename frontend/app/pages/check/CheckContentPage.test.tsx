/**
 * Everyone's check page beside the chat: the check while the provenance
 * module is on, a notice while it is off or the user may not check.
 */
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "~/api/http";
import * as provenance from "~/api/provenance";
import { renderPage } from "~/test/renderPage";

import CheckContentPage from "./CheckContentPage";

vi.mock("~/api/provenance", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/provenance")>()),
  fetchContentCheckStatus: vi.fn(),
  checkContent: vi.fn(),
}));

const status = vi.mocked(provenance.fetchContentCheckStatus);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CheckContentPage", () => {
  it("offers the check while the module is on", async () => {
    status.mockResolvedValue({ enabled: true, max_bytes: 20 * 1024 * 1024 });
    renderPage(<CheckContentPage />, { path: "/check-content" });

    expect(await screen.findByRole("textbox", { name: "Text to check" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Picture or file" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Check content" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to the chat" })).toBeInTheDocument();
  });

  it("says so while the module is off", async () => {
    status.mockResolvedValue({ enabled: false, max_bytes: 20 * 1024 * 1024 });
    renderPage(<CheckContentPage />, { path: "/check-content" });

    expect(await screen.findByText(/the provenance module is switched off/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Text to check" })).not.toBeInTheDocument();
  });

  it("says so when the user may not check", async () => {
    status.mockRejectedValue(new ApiError("Permission denied: provenance:check", 403));
    renderPage(<CheckContentPage />, { path: "/check-content" });

    expect(await screen.findByText(/You do not have the right to check content/)).toBeInTheDocument();
  });
});
