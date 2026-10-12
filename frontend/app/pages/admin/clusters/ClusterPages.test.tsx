/**
 * The cluster screens, end to end against mocked endpoints.
 *
 * The rework's claim is that an operator is never left guessing what to do,
 * so the assertions are mostly about the next-step banner being right and the
 * machinery having moved out of the way — not about pixels.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { inferenceApi } from "~/api/inference";
import { marketplaceApi } from "~/api/marketplace";
import { models as modelsApi } from "~/api/llm";
import { nodesApi } from "~/api/nodes";
import {
  ENV_ID,
  HEAD_NODE_ID,
  MODEL_ID,
  WORKER_NODE_ID,
  controlPlane,
  deployment,
  endpoints,
  environment,
  environmentMetricsPartial,
  environmentNodes,
  managedNodes,
  mixedComputePools,
  computePools,
  plan,
} from "~/test/inferenceFixtures";
import { renderPage } from "~/test/renderPage";

import ClusterDetailPage from "./ClusterDetailPage";
import ClustersPage from "./ClustersPage";

const MODEL = {
  id: MODEL_ID,
  display_name: "Qwen3-8B",
  source: "huggingface",
  hf_repo_id: "Qwen/Qwen3-8B",
  hf_revision: "main",
  license_ack_required: false,
  tags: null,
  status: "available",
  instances: [],
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function mockFleet() {
  vi.spyOn(inferenceApi, "listEnvironments").mockResolvedValue([environment]);
  vi.spyOn(inferenceApi, "listEnvironmentNodes").mockResolvedValue(environmentNodes);
  vi.spyOn(inferenceApi, "listEnvironmentPools").mockResolvedValue(computePools);
  vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([controlPlane]);
  vi.spyOn(inferenceApi, "listDrivers").mockResolvedValue(["ray"]);
  vi.spyOn(nodesApi, "list").mockResolvedValue(managedNodes);
  vi.spyOn(inferenceApi, "foundClusters").mockResolvedValue({ clusters: [], unreadable: [] });
}

function mockClusterDetail() {
  mockFleet();
  vi.spyOn(inferenceApi, "getEnvironment").mockResolvedValue(environment);
  vi.spyOn(inferenceApi, "listDeployments").mockResolvedValue([deployment]);
  vi.spyOn(inferenceApi, "listEndpoints").mockResolvedValue(endpoints);
  vi.spyOn(inferenceApi, "environmentMetrics").mockResolvedValue(
    environmentMetricsPartial,
  );
  vi.spyOn(modelsApi, "list").mockResolvedValue([MODEL] as never);
}

async function renderDetail() {
  renderPage(<ClusterDetailPage />, {
    path: "/admin/clusters/:id",
    initialPath: `/admin/clusters/${ENV_ID}`,
  });
  await screen.findByRole("heading", { name: "dgx-pair" });
}

describe("ClustersPage", () => {
  beforeEach(mockFleet);

  it("names the cluster and its machines without domain jargon", async () => {
    renderPage(<ClustersPage />);

    // The name arrives with the cluster list; the summary needs one call per
    // cluster to count its members, so it lands a moment later. That order is
    // deliberate -- the list no longer waits on the fan-out -- which is why
    // this awaits the summary rather than expecting it to be there already.
    expect(await screen.findByText("dgx-pair")).toBeInTheDocument();
    expect(await screen.findByText("2 machines · 1 leads")).toBeInTheDocument();
    expect(screen.queryByText(/environment/i)).not.toBeInTheDocument();
  });

  it("sends an operator with no machines to onboarding", async () => {
    vi.spyOn(nodesApi, "list").mockResolvedValue([]);
    vi.spyOn(inferenceApi, "listEnvironments").mockResolvedValue([]);
    renderPage(<ClustersPage />);

    const banner = await screen.findByTestId("next-step");
    expect(banner).toHaveAttribute("data-stage", "no-nodes");
    expect(within(banner).getByText("Add your first machine")).toBeInTheDocument();
  });

  it("offers to create a cluster once machines exist", async () => {
    vi.spyOn(inferenceApi, "listEnvironments").mockResolvedValue([]);
    renderPage(<ClustersPage />);

    const banner = await screen.findByTestId("next-step");
    expect(banner).toHaveAttribute("data-stage", "no-cluster");
    await userEvent.click(within(banner).getByRole("button", { name: "Create a cluster" }));
    expect(await screen.findByText("Name it")).toBeInTheDocument();
  });
});

describe("ClusterDetailPage", () => {
  beforeEach(mockClusterDetail);

  it("draws the cluster, with the head at its centre", async () => {
    await renderDetail();

    const picture = screen.getByRole("img", { name: "Cluster topology" });
    expect(within(picture).getByText("gpu-02")).toBeInTheDocument();
    expect(within(picture).getByText("leads the cluster")).toBeInTheDocument();
    expect(within(picture).getByText("gpu-01")).toBeInTheDocument();
    // The link an operator cares about, drawn on the edge.
    expect(within(picture).getByText(/roce · 200 Gb\/s/)).toBeInTheDocument();
  });

  it("renders the page before the slow calls have answered", async () => {
    // The case this exists for: a cluster whose nodes are unwell is exactly
    // when metrics hangs, and exactly when the operator needs the screen.
    // It used to be one Promise.all behind one spinner, so they waited
    // longest for the page that would have told them what was wrong.
    vi.spyOn(inferenceApi, "environmentMetrics").mockReturnValue(
      new Promise(() => {}) as never,
    );
    vi.spyOn(inferenceApi, "listDeployments").mockReturnValue(
      new Promise(() => {}) as never,
    );

    renderPage(<ClusterDetailPage />, {
      path: "/admin/clusters/:id",
      initialPath: `/admin/clusters/${ENV_ID}`,
    });

    // The frame arrives on the strength of the cluster call alone.
    expect(await screen.findByRole("heading", { name: "dgx-pair" })).toBeInTheDocument();
    expect(screen.getByText("How this cluster is wired")).toBeInTheDocument();
    expect(screen.getByText("Models on this cluster")).toBeInTheDocument();
  });

  it("keeps working when one section fails and the others do not", async () => {
    vi.spyOn(inferenceApi, "listDeployments").mockRejectedValue(
      new Error("deployments unavailable"),
    );
    await renderDetail();

    // The failure is reported where it happened...
    expect(await screen.findByText("deployments unavailable")).toBeInTheDocument();
    // ...and the rest of the page is still usable.
    const picture = screen.getByRole("img", { name: "Cluster topology" });
    expect(within(picture).getByText("gpu-02")).toBeInTheDocument();
  });

  it("never shows a missing number as a zero", async () => {
    // A gap at load time is the same lie as a gap at render time.
    vi.spyOn(inferenceApi, "listEnvironmentNodes").mockReturnValue(
      new Promise(() => {}) as never,
    );
    vi.spyOn(inferenceApi, "environmentMetrics").mockReturnValue(
      new Promise(() => {}) as never,
    );

    renderPage(<ClusterDetailPage />, {
      path: "/admin/clusters/:id",
      initialPath: `/admin/clusters/${ENV_ID}`,
    });
    await screen.findByRole("heading", { name: "dgx-pair" });

    expect(screen.queryByText("0 of 0 up")).not.toBeInTheDocument();
  });

  it("says nothing about machine groups when every machine is alike", async () => {
    // The derivation always produces a pool; a cluster of identical machines
    // gets exactly one, and a grouping with one group in it is noise. The
    // operator should never have to learn the word.
    await renderDetail();

    expect(screen.queryByText(/kinds of machine/i)).not.toBeInTheDocument();
    expect(screen.queryByText("gb10")).not.toBeInTheDocument();
  });

  it("names the groups once a cluster holds more than one kind of machine", async () => {
    vi.spyOn(inferenceApi, "listEnvironmentPools").mockResolvedValue(mixedComputePools);
    await renderDetail();

    expect(await screen.findByText("2 kinds of machine")).toBeInTheDocument();
    expect(screen.getByText("gb10")).toBeInTheDocument();
    expect(screen.getByText("mi300x")).toBeInTheDocument();
    // Hardware, architecture and size -- not the matching signature, which is
    // a key rather than a sentence.
    expect(screen.getByText("GB10 · aarch64 · 2 machines")).toBeInTheDocument();
    expect(screen.queryByText(/nvidia\/aarch64\/gb10/)).not.toBeInTheDocument();
  });

  it("tells you which group a machine you clicked is in", async () => {
    vi.spyOn(inferenceApi, "listEnvironmentPools").mockResolvedValue(mixedComputePools);
    await renderDetail();

    const picture = screen.getByRole("img", { name: "Cluster topology" });
    await userEvent.click(within(picture).getByText("gpu-01"));

    expect(await screen.findByText("Machine group")).toBeInTheDocument();
  });

  it("says the cluster is serving rather than showing a phase name", async () => {
    await renderDetail();

    const banner = await screen.findByTestId("next-step");
    expect(banner).toHaveAttribute("data-stage", "serving");
  });

  it("offers a deployment when the cluster is running and empty", async () => {
    vi.spyOn(inferenceApi, "listDeployments").mockResolvedValue([]);
    await renderDetail();

    const banner = await screen.findByTestId("next-step");
    expect(banner).toHaveAttribute("data-stage", "ready");
    vi.spyOn(marketplaceApi, "clusters").mockResolvedValue([]);
    await userEvent.click(within(banner).getByRole("button", { name: "Deploy a model" }));
    // The host dialog, opened on this cluster, starts by asking which kept model.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Pick one of the models this server keeps/)).toBeInTheDocument();
  });

  it("offers the network step when none has been applied", async () => {
    vi.spyOn(inferenceApi, "getEnvironment").mockResolvedValue({
      ...environment,
      observed_status: {},
    });
    vi.spyOn(inferenceApi, "planEnvironment").mockResolvedValue(plan);
    await renderDetail();

    const banner = await screen.findByTestId("next-step");
    expect(banner).toHaveAttribute("data-stage", "no-network");
    await userEvent.click(
      within(banner).getByRole("button", { name: "Choose the network" }),
    );
    // The same evidence the wizard shows, reachable after the fact.
    expect(await screen.findByText("recommended")).toBeInTheDocument();
  });

  it("keeps the machinery, but behind Advanced", async () => {
    await renderDetail();

    // Not on the page at rest…
    expect(screen.queryByText("HeadActive")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Advanced"));
    // …but one click away, unchanged.
    expect(await screen.findByText("HeadActive")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show raw provider status" }),
    ).toBeInTheDocument();
  });

  /** What the server answers for removing or draining the worker. */
  function removalPlan(copies: Array<{ moves: boolean; reason?: string }>) {
    return {
      node_id: WORKER_NODE_ID,
      machine: "gpu-01",
      draining: false,
      in_cluster: true,
      environment_id: ENV_ID,
      environment_name: "dgx-pair",
      cluster_running: true,
      ray_node_id: "w".repeat(56),
      copies: copies.map((c, i) => ({
        app: `faden-${i}`,
        deployment_id: String(i),
        name: `qwen-${i}`,
        replica_id: `r${i}`,
        actor_name: `a${i}`,
        state: "RUNNING",
        gpus: 0.5,
        spans: ["gpu-01"],
        moves: c.moves,
        to: c.moves ? ["gpu-02"] : [],
        reason: c.reason ?? null,
      })),
      moving: copies.filter((c) => c.moves).length,
      stopping: copies.filter((c) => !c.moves).length,
      other_parts: 0,
      can_drain: true,
      reason: null,
    };
  }

  it("removes a machine only after showing where its copies go", async () => {
    vi.spyOn(inferenceApi, "memberRemovalPlan").mockResolvedValue(removalPlan([{ moves: true }]));
    const remove = vi
      .spyOn(inferenceApi, "removeMember")
      .mockResolvedValue({ removed: false, pending: true });
    await renderDetail();

    await userEvent.click(
      screen.getByRole("button", { name: "gpu-01, worker" }),
    );
    expect(await screen.findByText(/Worker · 192.0.2.71/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove from cluster" }));
    expect(await screen.findByText("Moves to gpu-02.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith(ENV_ID, WORKER_NODE_ID, false),
    );
  });

  it("says which copies would stop, and stops them only when confirmed", async () => {
    vi.spyOn(inferenceApi, "memberRemovalPlan").mockResolvedValue(
      removalPlan([{ moves: true }, { moves: false, reason: "It needs 1 GPU; the most another machine has free is 0 GPU." }]),
    );
    const remove = vi
      .spyOn(inferenceApi, "removeMember")
      .mockResolvedValue({ removed: false, pending: true });
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "gpu-01, worker" }));
    await userEvent.click(await screen.findByRole("button", { name: "Remove from cluster" }));
    expect(await screen.findByText(/Stops: It needs 1 GPU/)).toBeInTheDocument();
    expect(screen.getByText(/Copies that cannot move: 1/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove and stop 1" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(ENV_ID, WORKER_NODE_ID, true));
  });

  it("does not offer to remove the head while the cluster runs", async () => {
    vi.spyOn(inferenceApi, "memberRemovalPlan").mockResolvedValue({
      ...removalPlan([]),
      node_id: HEAD_NODE_ID,
      can_drain: false,
      reason: "gpu-02 is the cluster's head. Stop the cluster first to remove it.",
    });
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "gpu-02, head" }));
    await userEvent.click(await screen.findByRole("button", { name: "Remove from cluster" }));
    expect(await screen.findByText(/is the cluster's head/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("adds machines that can join and says why the others cannot", async () => {
    vi.spyOn(inferenceApi, "listMemberCandidates").mockResolvedValue({
      environment_id: ENV_ID,
      cluster_running: true,
      network: "10.100.0.0/24",
      machines: [
        { node_id: "n1", machine: "spark-3", host: "192.0.2.80", gpus: 1, can_add: true, reasons: [], warnings: [] },
        { node_id: "n2", machine: "laptop", host: "192.0.2.90", gpus: 0, can_add: false,
          reasons: ["It has no address on the cluster's network (10.100.0.0/24)."], warnings: [] },
      ],
    });
    const add = vi.spyOn(inferenceApi, "addMembers").mockResolvedValue({ environment_id: ENV_ID, added: ["n1"] });
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "Add machines" }));
    expect(await screen.findByText(/no address on the cluster's network/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "laptop" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "spark-3" }));
    await userEvent.click(screen.getByRole("button", { name: "Add (1)" }));

    await waitFor(() => expect(add).toHaveBeenCalledWith(ENV_ID, ["n1"]));
  });

  it("shows a drain under way and lets it be stopped", async () => {
    vi.spyOn(inferenceApi, "listEnvironmentNodes").mockResolvedValue(
      environmentNodes.map((m) =>
        m.node_id === WORKER_NODE_ID
          ? { ...m, observed: { drain: { state: "moving", remove: false, message: "Still moving 1 copy.", left: 1, copies: [] } } }
          : m,
      ),
    );
    const stop = vi.spyOn(nodesApi, "setDrain").mockResolvedValue(managedNodes[0] as never);
    await renderDetail();

    expect(await screen.findByText(/Moving copies: Still moving 1 copy\./)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop drain" }));
    await waitFor(() => expect(stop).toHaveBeenCalledWith(WORKER_NODE_ID, false));
  });

  it("does not claim which machine runs which copy", async () => {
    await renderDetail();
    await userEvent.click(
      screen.getByRole("button", { name: "gpu-02, head" }),
    );

    expect(
      await screen.findByText(/not reported by the runtime/),
    ).toBeInTheDocument();
  });

  it("stops the cluster through desired state, once confirmed", async () => {
    const update = vi
      .spyOn(inferenceApi, "updateEnvironment")
      .mockResolvedValue(environment);
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    // The button stands where Start stood: a click alone must not stop it.
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByText(/Every model on it stops serving/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Stop cluster" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(ENV_ID, { desired_state: "stopped" }),
    );
  });

  it("does not stop the cluster when the stop is cancelled", async () => {
    const update = vi
      .spyOn(inferenceApi, "updateEnvironment")
      .mockResolvedValue(environment);
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(update).not.toHaveBeenCalled();
    expect(screen.queryByText(/Every model on it stops serving/)).not.toBeInTheDocument();
  });

  it("still surfaces the worker-metrics partial, in Advanced", async () => {
    await renderDetail();
    await userEvent.click(screen.getByText("Advanced"));

    expect(await screen.findByText("Partial metrics")).toBeInTheDocument();
    expect(screen.getByText("node_metrics")).toBeInTheDocument();
  });

  it("uses the head node id to centre the picture", async () => {
    await renderDetail();
    const picture = screen.getByRole("img", { name: "Cluster topology" });
    // The head fixture is the one bound as head_node_id.
    expect(environment.head_node_id).toBe(HEAD_NODE_ID);
    expect(within(picture).getByRole("button", { name: /gpu-02/ })).toBeInTheDocument();
  });
});
