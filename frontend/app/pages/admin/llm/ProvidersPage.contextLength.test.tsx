/**
 * The provider's edit form takes the model's context length: empty is null (what the server says),
 * a number is checked against the range before it is saved.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as llm from "~/api/llm";
import type { Provider } from "~/api/llm";
import { observability } from "~/api/observability";
import { renderPage } from "~/test/renderPage";

import ProvidersPage, { contextLengthOf } from "./ProvidersPage";

function provider(extra: Partial<Provider> = {}): Provider {
  return {
    id: "p1", name: "Remote qwen", type: "remote", target: "remote_endpoint",
    endpoint_url: "https://api.example.com/v1", capabilities: null, remote_model: "qwen", litellm_provider: null,
    litellm_model: null, extra_params: null, source_kind: null, source_id: null, managed_by: null,
    ...extra,
  } as Provider;
}

async function openEdit() {
  renderPage(<ProvidersPage />, { path: "/admin/llm/providers" });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  return within(await screen.findByRole("dialog"));
}

beforeEach(() => {
  vi.spyOn(llm.providers, "list").mockResolvedValue([provider()]);
  vi.spyOn(llm.providers, "update").mockResolvedValue(provider());
  vi.spyOn(llm.runtimes, "list").mockResolvedValue([]);
  vi.spyOn(llm.models, "list").mockResolvedValue([]);
  vi.spyOn(observability, "listPricing").mockResolvedValue([]);
});

describe("contextLengthOf", () => {
  it.each([
    ["", null],
    ["  ", null],
    ["1024", 1024],
    ["10000000", 10_000_000],
    ["1023", undefined],
    ["10000001", undefined],
    ["12k", undefined],
    ["8192.5", undefined],
    ["-4096", undefined],
  ])("reads %j as %j", (raw, expected) => {
    expect(contextLengthOf(raw)).toBe(expected);
  });
});

describe("Providers > edit > context length", () => {
  it("is empty with the helper text when none was given, and saves nothing for it", async () => {
    const dialog = await openEdit();
    const field = dialog.getByLabelText("Context length (tokens)");
    expect(field).toHaveValue("");
    expect(dialog.getByText("Empty: as the model's server reports it.")).toBeInTheDocument();

    fireEvent.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(llm.providers.update).toHaveBeenCalled());
    expect(vi.mocked(llm.providers.update).mock.calls[0][1]).not.toHaveProperty("context_length");
  });

  it("saves a number in range", async () => {
    const dialog = await openEdit();
    fireEvent.change(dialog.getByLabelText("Context length (tokens)"), { target: { value: "32768" } });
    fireEvent.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(llm.providers.update).toHaveBeenCalledWith("p1", expect.objectContaining({ context_length: 32768 })),
    );
  });

  it("refuses a number out of range before it is saved", async () => {
    const dialog = await openEdit();
    fireEvent.change(dialog.getByLabelText("Context length (tokens)"), { target: { value: "512" } });
    expect(dialog.getByText(/A whole number from 1,024 to 10,000,000/)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(dialog.getByLabelText("Context length (tokens)"), { target: { value: "20000000" } });
    expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(llm.providers.update).not.toHaveBeenCalled();
  });

  it("clears it with an empty field: null", async () => {
    vi.spyOn(llm.providers, "list").mockResolvedValue([provider({ context_length: 32768 })]);
    const dialog = await openEdit();
    const field = dialog.getByLabelText("Context length (tokens)");
    expect(field).toHaveValue("32768");
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(dialog.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(llm.providers.update).toHaveBeenCalledWith("p1", expect.objectContaining({ context_length: null })),
    );
  });
});
