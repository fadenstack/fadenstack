/**
 * Models a cluster runs without a deployment here: the health check's hint,
 * and removing one -- confirmed first, since it stops a model that may still
 * be answering requests. Which apps may be removed is the backend's check
 * (test_inference_takeover.py); here, what the operator sees and what is sent.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type FoundClusterApp } from "~/api/inference";

import StrayModelsCard from "./StrayModelsCard";

const ENV = "33333333-3333-3333-3333-333333333333";

const STRAY: FoundClusterApp = {
  app_name: "faden-a34b195a-d92a-4bc8-9d74-6b7aa7919b4a",
  deployment_id: "a34b195a-d92a-4bc8-9d74-6b7aa7919b4a",
  model_id: "Qwen3-0.6B",
  model_source: "/root/.cache/huggingface/models--Qwen--Qwen3-0.6B/snapshots/c1899de",
  hf_repo_id: "Qwen/Qwen3-0.6B",
  copies: 1,
  gpus_per_copy: 1,
  engine: {},
  status: "RUNNING",
  running_copies: 1,
  suggested_alias: "qwen3-0.6b",
  notes: [],
  already_known: false,
};

afterEach(() => vi.restoreAllMocks());

describe("StrayModelsCard", () => {
  it("says when the last health check found models no deployment owns", () => {
    render(<StrayModelsCard environmentId={ENV} noted={[STRAY.app_name]} />);
    expect(screen.getByTestId("stray-models-noted")).toHaveTextContent(
      "found a model running here with no deployment",
    );
  });

  it("says nothing before anyone looks when the health check found none", () => {
    render(<StrayModelsCard environmentId={ENV} />);
    expect(screen.queryByTestId("stray-models-noted")).toBeNull();
  });

  it("removes a model only once confirmed", async () => {
    vi.spyOn(inferenceApi, "strayModels").mockResolvedValue({ models: [STRAY], error: null });
    const remove = vi
      .spyOn(inferenceApi, "removeStrayModels")
      .mockResolvedValue({ environment_id: ENV, removing: [STRAY.app_name] });
    render(<StrayModelsCard environmentId={ENV} noted={[STRAY.app_name]} />);

    await userEvent.click(screen.getByRole("button", { name: "Look for them" }));
    expect(await screen.findByText(STRAY.model_id)).toBeInTheDocument();
    expect(screen.queryByTestId("stray-models-noted")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(STRAY.app_name);
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));

    expect(remove).toHaveBeenCalledWith(ENV, [STRAY.app_name]);
    expect(await screen.findByText(/is being removed from the cluster/)).toBeInTheDocument();
    expect(screen.queryByText(STRAY.app_name)).toBeNull();
  });
});
