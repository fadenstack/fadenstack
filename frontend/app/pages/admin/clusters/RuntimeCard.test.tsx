/**
 * The runtime a cluster runs: what its machines run, a newer certified one
 * announced but never applied by itself, and a switch -- to a preview too --
 * that restarts the cluster only after a confirmation. What may be chosen is
 * the backend's test.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type ClusterRuntime, type ClusterRuntimeStatus } from "~/api/inference";

import RuntimeCard from "./RuntimeCard";

const ENV = "89ed5f75-078c-4d9a-8ac7-09255fe29db6";
const OLDER = "faden-ray-vllm-gb10-ray2.58-nv26.07";
const STABLE = "faden-ray-vllm-gb10-ray2.58-nv26.08";
const PREVIEW = "faden-ray-vllm-gb10-ray2.58-nv26.09";

function runtime(id: string, vllm: string, overrides: Partial<ClusterRuntime> = {}): ClusterRuntime {
  return {
    bundle_id: id,
    image: `faden/ray-vllm-gb10:${id.slice(-14)}`,
    runtime_version: "2.58.0",
    vllm_version: vllm,
    certification_status: "passed",
    channel: "stable",
    default: false,
    ...overrides,
  };
}

function status(runs: string, overrides: Partial<ClusterRuntimeStatus> = {}): ClusterRuntimeStatus {
  return {
    runs: [runs],
    applied: true,
    restarting: false,
    update: null,
    runtimes: [
      runtime(PREVIEW, "0.29.0", { channel: "preview" }),
      runtime(STABLE, "0.27.1", { default: true }),
      runtime(OLDER, "0.26.2"),
    ],
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("RuntimeCard", () => {
  it("shows what the machines run, recommended and certified", async () => {
    vi.spyOn(inferenceApi, "runtime").mockResolvedValue(status(STABLE));
    render(<RuntimeCard environmentId={ENV} />);

    expect(await screen.findByText("Ray 2.58.0 · vLLM 0.27.1")).toBeInTheDocument();
    const card = screen.getByTestId("cluster-runtime");
    expect(within(card).getAllByText("Certified").length).toBeGreaterThan(0);
    expect(within(card).getByText("Recommended")).toBeInTheDocument();
    expect(screen.queryByTestId("runtime-update")).not.toBeInTheDocument();
  });

  it("announces a newer certified runtime and switches only after a confirmation", async () => {
    vi.spyOn(inferenceApi, "runtime").mockResolvedValue(status(OLDER, { update: STABLE }));
    const switched = vi.spyOn(inferenceApi, "switchRuntime").mockResolvedValue(status(STABLE));
    const onSwitched = vi.fn();
    render(<RuntimeCard environmentId={ENV} onSwitched={onSwitched} />);

    const update = await screen.findByTestId("runtime-update");
    expect(update).toHaveTextContent("vLLM 0.27.1");
    expect(switched).not.toHaveBeenCalled();

    await userEvent.click(within(update).getByRole("button", { name: "Switch" }));
    expect(screen.getByText(/restarts the cluster/)).toBeInTheDocument();
    vi.mocked(inferenceApi.runtime).mockResolvedValue(status(STABLE, { applied: false, restarting: true }));
    await userEvent.click(screen.getByRole("button", { name: "Restart on this runtime" }));

    expect(switched).toHaveBeenCalledWith(ENV, STABLE);
    expect(onSwitched).toHaveBeenCalled();
    expect(await screen.findByText("Restarting on the new runtime")).toBeInTheDocument();
  });

  it("offers the preview, and says what choosing it means", async () => {
    vi.spyOn(inferenceApi, "runtime").mockResolvedValue(status(STABLE));
    const switched = vi.spyOn(inferenceApi, "switchRuntime").mockResolvedValue(status(PREVIEW));
    render(<RuntimeCard environmentId={ENV} />);

    await userEvent.click(await screen.findByRole("combobox"));
    const running = screen.getByRole("option", { name: /vLLM 0\.27\.1/ });
    expect(running).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(screen.getByRole("option", { name: /vLLM 0\.29\.0/ }));
    await userEvent.click(screen.getByRole("button", { name: "Switch" }));

    expect(screen.getByText(/A preview has passed its certification/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restart on this runtime" }));
    await waitFor(() => expect(switched).toHaveBeenCalledWith(ENV, PREVIEW));
  });

  it("says a stopped cluster takes the switch when it starts, without polling", async () => {
    const asked = vi.spyOn(inferenceApi, "runtime").mockResolvedValue(status(PREVIEW, { applied: false }));
    render(<RuntimeCard environmentId={ENV} />);
    expect(await screen.findByText("Applies when the cluster starts")).toBeInTheDocument();
    expect(screen.queryByText("Restarting on the new runtime")).not.toBeInTheDocument();
    expect(asked).toHaveBeenCalledTimes(1);
  });

  it("says nothing to an older server that has no runtime choice", async () => {
    vi.spyOn(inferenceApi, "runtime").mockRejectedValue(new Error("Not Found"));
    const { container } = render(<RuntimeCard environmentId={ENV} />);
    await waitFor(() => expect(inferenceApi.runtime).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
