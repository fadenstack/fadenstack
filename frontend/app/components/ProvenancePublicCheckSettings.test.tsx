/**
 * The public check page's settings in the provenance card: the switch saves
 * when flipped, the contact with its button, and the page's address shows
 * while it is offered.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { systemSettingsApi } from "~/api/systemSettings";

import ProvenancePublicCheckSettings, {
  PUBLIC_CHECK_CONTACT_KEY,
  PUBLIC_CHECK_ENABLED_KEY,
} from "./ProvenancePublicCheckSettings";

const update = vi.fn(async (key: string) => ({
  key,
  apply_status: "success",
  apply_scope: "live_reload",
  apply_job_id: null,
  messages: [],
}));

function stored(enabled: boolean, contact: string) {
  vi.spyOn(systemSettingsApi, "values").mockResolvedValue({
    items: {
      [PUBLIC_CHECK_ENABLED_KEY]: { is_secret: false, value: enabled },
      [PUBLIC_CHECK_CONTACT_KEY]: { is_secret: false, value: contact },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(systemSettingsApi, "update").mockImplementation(update);
});

describe("ProvenancePublicCheckSettings", () => {
  it("is off by default and switches the page on", async () => {
    stored(false, "");
    const user = userEvent.setup();
    render(<ProvenancePublicCheckSettings />);

    const toggle = await screen.findByRole("switch", { name: "Offer a public check page" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(screen.queryByTestId("provenance-public-check-link")).not.toBeInTheDocument();

    await user.click(toggle);
    await waitFor(() => expect(update).toHaveBeenCalledWith(PUBLIC_CHECK_ENABLED_KEY, true, "local"));
    expect(await screen.findByText("The public check page is now offered.")).toBeInTheDocument();
    expect(screen.getByTestId("provenance-public-check-link")).toHaveTextContent("/check");
  });

  it("saves the contact for text checks", async () => {
    stored(true, "old@example.org");
    const user = userEvent.setup();
    render(<ProvenancePublicCheckSettings />);

    const field = await screen.findByRole("textbox", { name: "Contact for text checks" });
    await waitFor(() => expect(field).toHaveValue("old@example.org"));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.clear(field);
    await user.type(field, "  provenance@example.org ");
    await user.click(save);
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(PUBLIC_CHECK_CONTACT_KEY, "provenance@example.org", "local"),
    );
    expect(await screen.findByText("The contact is saved.")).toBeInTheDocument();
  });

  it("says so when a setting cannot be saved", async () => {
    stored(false, "");
    vi.spyOn(systemSettingsApi, "update").mockRejectedValue(new Error("API 403"));
    const user = userEvent.setup();
    render(<ProvenancePublicCheckSettings />);

    const toggle = await screen.findByRole("switch", { name: "Offer a public check page" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await user.click(toggle);
    expect(await screen.findByText("The setting could not be saved.")).toBeInTheDocument();
    expect(toggle).not.toBeChecked();
  });
});
