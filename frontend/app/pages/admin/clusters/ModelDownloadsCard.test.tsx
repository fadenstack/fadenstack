/**
 * The switch that lets a cluster's machines download models themselves: off
 * by default, and unusable -- with a tooltip naming the machine and why --
 * while a machine cannot reach Hugging Face. The server refuses it as well;
 * that is the backend's test.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type MachineInternet, type ModelDownloadsStatus } from "~/api/inference";

import ModelDownloadsCard from "./ModelDownloadsCard";

const ENV = "89ed5f75-078c-4d9a-8ac7-09255fe29db6";

function machine(host: string, internet: boolean | null, problem: MachineInternet["problem"] = null): MachineInternet {
  return {
    node_id: `node-${host}`,
    host,
    internet,
    detail: internet ? "Reached https://huggingface.co/api/models?limit=1 in 180 ms" : "ConnectTimeout: timed out",
    checked_at: "2026-09-28T15:00:00+00:00",
    problem,
  };
}

function status(overrides: Partial<ModelDownloadsStatus> = {}): ModelDownloadsStatus {
  return {
    on: false,
    machines: [machine("192.0.2.71", true), machine("192.0.2.49", true)],
    can_switch_on: true,
    reason: null,
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("ModelDownloadsCard", () => {
  it("is off by default and can be switched on when every machine reaches Hugging Face", async () => {
    vi.spyOn(inferenceApi, "modelDownloads").mockResolvedValue(status());
    const flip = vi.spyOn(inferenceApi, "switchModelDownloads").mockResolvedValue(status({ on: true }));
    render(<ModelDownloadsCard environmentId={ENV} />);

    const toggle = await screen.findByRole("switch", { name: "Machines download models themselves" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(screen.getByText("From this server")).toBeInTheDocument();
    expect(screen.getByText("192.0.2.49: Reaches Hugging Face")).toBeInTheDocument();

    await userEvent.click(toggle);

    expect(flip).toHaveBeenCalledWith(ENV, true);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(screen.getByText("From Hugging Face")).toBeInTheDocument();
  });

  it("stays off, with the machine that cannot named in its tooltip", async () => {
    vi.spyOn(inferenceApi, "modelDownloads").mockResolvedValue(
      status({
        machines: [machine("192.0.2.71", true), machine("192.0.2.49", false, "no_internet")],
        can_switch_on: false,
        reason: "Every machine must reach Hugging Face for this. 192.0.2.49: cannot reach Hugging Face (ConnectTimeout)",
      }),
    );
    const flip = vi.spyOn(inferenceApi, "switchModelDownloads");
    render(<ModelDownloadsCard environmentId={ENV} />);

    const toggle = await screen.findByRole("switch", { name: "Machines download models themselves" });
    await waitFor(() => expect(screen.getByText("192.0.2.49: cannot reach Hugging Face")).toBeInTheDocument());
    expect(toggle).toBeDisabled();

    await userEvent.hover(screen.getByTestId("model-downloads-switch"));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Every machine must reach Hugging Face to switch this on");
    expect(tooltip).toHaveTextContent("192.0.2.49: cannot reach Hugging Face");
    expect(tooltip).not.toHaveTextContent("192.0.2.71");
    expect(flip).not.toHaveBeenCalled();
  });

  it("says which agent is too old", async () => {
    vi.spyOn(inferenceApi, "modelDownloads").mockResolvedValue(
      status({ machines: [machine("192.0.2.49", null, "old_agent")], can_switch_on: false, reason: "old" }),
    );
    render(<ModelDownloadsCard environmentId={ENV} />);
    expect(
      await screen.findByText("192.0.2.49: agent too old, update it to 0.2.2 or later"),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Machines download models themselves" })).toBeDisabled();
  });

  it("can always be switched off, whatever the machines answer now", async () => {
    vi.spyOn(inferenceApi, "modelDownloads").mockResolvedValue(
      status({ on: true, machines: [machine("192.0.2.49", false, "no_internet")], can_switch_on: false }),
    );
    const flip = vi.spyOn(inferenceApi, "switchModelDownloads").mockResolvedValue(
      status({ on: false, machines: [machine("192.0.2.49", false, "no_internet")], can_switch_on: false }),
    );
    render(<ModelDownloadsCard environmentId={ENV} />);

    const toggle = await screen.findByRole("switch", { name: "Machines download models themselves" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);
    expect(flip).toHaveBeenCalledWith(ENV, false);
  });

  it("shows the server's refusal and asks the machines again", async () => {
    const load = vi.spyOn(inferenceApi, "modelDownloads").mockResolvedValue(status());
    vi.spyOn(inferenceApi, "switchModelDownloads").mockRejectedValue(
      new Error("Every machine must reach Hugging Face for this. 192.0.2.49: did not answer"),
    );
    render(<ModelDownloadsCard environmentId={ENV} />);

    const toggle = await screen.findByRole("switch", { name: "Machines download models themselves" });
    await waitFor(() => expect(toggle).toBeEnabled());
    await userEvent.click(toggle);

    expect(await screen.findByText(/192.0.2.49: did not answer/)).toBeInTheDocument();
    await waitFor(() => expect(load).toHaveBeenLastCalledWith(ENV, true));
  });

  it("stays out of the way on a server that does not know it", async () => {
    const spy = vi.spyOn(inferenceApi, "modelDownloads").mockRejectedValue(new Error("404"));
    const { container } = render(<ModelDownloadsCard environmentId={ENV} />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
