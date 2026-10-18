/**
 * The public check page (/check): offered, it takes a picture or a file and
 * names whom to ask for a text check; not offered, it says so and nothing
 * else.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "~/api/http";
import * as provenance from "~/api/provenance";

import PublicCheckPage from "./check";

vi.mock("~/api/provenance", async (importOriginal) => ({
  ...(await importOriginal<typeof import("~/api/provenance")>()),
  fetchPublicCheck: vi.fn(),
  checkContentPublicly: vi.fn(),
}));

const info = vi.mocked(provenance.fetchPublicCheck);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/check"]}>
      <PublicCheckPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("the public check page", () => {
  it("says it is not offered when the server does not offer it", async () => {
    info.mockRejectedValue(new ApiError("Not Found", 404));
    renderPage();

    expect(await screen.findByTestId("public-check-not-offered")).toHaveTextContent(
      "Content checks are not offered here.",
    );
    expect(screen.queryByTestId("content-check-file")).not.toBeInTheDocument();
  });

  it("takes a picture or a file and names whom to ask for a text check", async () => {
    info.mockResolvedValue({ contact: "provenance@example.org", max_bytes: 20 * 1024 * 1024 });
    renderPage();

    expect(await screen.findByTestId("content-check-file")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Check a picture or a file" })).toBeInTheDocument();
    const texts = screen.getByTestId("public-check-texts");
    expect(texts).toHaveTextContent("Texts are checked for people with an account at this organisation.");
    expect(texts).toHaveTextContent("To have a text checked, contact: provenance@example.org");
    expect(screen.queryByRole("textbox", { name: "Text to check" })).not.toBeInTheDocument();
  });

  it("points at the organisation when no contact is named", async () => {
    info.mockResolvedValue({ contact: "", max_bytes: 20 * 1024 * 1024 });
    renderPage();

    expect(await screen.findByTestId("public-check-texts")).toHaveTextContent(
      "ask the organisation that runs this server",
    );
  });
});
