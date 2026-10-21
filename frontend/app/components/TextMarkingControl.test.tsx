/**
 * Marking a provider's chat answers on its security-map card: the status says what was set, or what happens
 * without it; the dialog saves evidence for a remote API, or a reason to answer unmarked, or clears both.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Provider, TextMarkingBody } from "~/api/llm";
import { readFadenEvent } from "~/pages/chat/hooks/useChatStream";

import { TextMarkingControl } from "./TextMarkingControl";

function provider(over: Partial<Provider> = {}): Provider {
  return {
    id: "p1", name: "Claude", type: "cloud", target: "remote_endpoint", endpoint_url: null, capabilities: null,
    remote_model: null, litellm_provider: "anthropic", litellm_model: null, extra_params: null, source_kind: null,
    source_id: null, managed_by: null, ...over,
  } as Provider;
}

describe("TextMarkingControl", () => {
  it("says what happens with nothing set, and offers no change without the right", () => {
    render(<TextMarkingControl provider={provider()} />);
    expect(screen.getByTestId("text-marking-p1")).toHaveTextContent(/status_none_remote|no evidence/);
    expect(screen.queryByTestId("text-marking-edit-p1")).toBeNull();
  });

  it("saves a reason to answer unmarked, and only a reason long enough", async () => {
    const onSave = vi.fn(async (_provider: Provider, _body: TextMarkingBody) => undefined);
    render(<TextMarkingControl provider={provider({ type: "vllm" })} onSave={onSave} />);
    await userEvent.click(screen.getByTestId("text-marking-edit-p1"));
    expect(screen.queryByLabelText(/choice_upstream|marks its models/)).toBeNull();
    await userEvent.click(screen.getByLabelText(/choice_allow|without a mark/));
    await userEvent.type(screen.getByTestId("text-marking-reason"), "short");
    expect(screen.getByTestId("text-marking-save")).toBeDisabled();
    await userEvent.type(screen.getByTestId("text-marking-reason"), " but now long enough");
    await userEvent.click(screen.getByTestId("text-marking-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][1]).toEqual({ allow_unmarked: { reason: "short but now long enough" } });
  });

  it("saves the evidence that a remote API marks its text", async () => {
    const onSave = vi.fn(async (_provider: Provider, _body: TextMarkingBody) => undefined);
    render(<TextMarkingControl provider={provider()} onSave={onSave} />);
    await userEvent.click(screen.getByTestId("text-marking-edit-p1"));
    await userEvent.click(screen.getByLabelText(/choice_upstream|marks its models/));
    await userEvent.type(screen.getByTestId("text-marking-url"), "https://example.com/marks");
    expect(screen.getByTestId("text-marking-save")).toBeDisabled(); // no date yet
  });
});

describe("the chat's notice", () => {
  it("reads an answer an administrator allowed without a mark", () => {
    expect(readFadenEvent({ type: "CUSTOM", name: "faden.unmarked", value: { reason: "allowed-by-administrator" } }))
      .toEqual({ kind: "unmarked" });
  });
});
