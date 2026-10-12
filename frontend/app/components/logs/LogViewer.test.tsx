/**
 * The log console: every line gets a level (its own, or what its text says),
 * the levels are filters with counts, and a Loki stream or a container's
 * console both become the same lines.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import LogViewer, { fromLokiStreams, fromText, levelsOf, type ViewerLine } from "./LogViewer";

const FAILED_START: ViewerLine[] = [
  { ts: "2026-09-30T06:44:00Z", level: "INFO", message: "Started LLMDeployment:qwen", source: "replica" },
  { ts: "2026-09-30T06:44:10Z", message: "(EngineCore pid=13728) WARNING 09-30 [cuda.py:1] low memory", source: "engine" },
  { ts: "2026-09-30T06:44:11Z", message: "(EngineCore pid=13728) Traceback (most recent call last):", source: "engine" },
  { ts: "2026-09-30T06:44:11Z", message: '(EngineCore pid=13728)   File "/opt/vllm/loader.py", line 7, in load', source: "engine" },
  {
    ts: "2026-09-30T06:44:12Z",
    message: "(EngineCore pid=13728) ValueError: There is no module or parameter named 'ngram_embedding.weight_scale'",
    source: "engine",
  },
];

describe("levelsOf", () => {
  it("reads a line's own level, else its text, and gives a traceback's frames the level of their exception", () => {
    expect(levelsOf(FAILED_START)).toEqual(["info", "warning", "error", "error", "error"]);
  });

  it("leaves a line that says nothing as other", () => {
    expect(levelsOf([{ message: "Loading safetensors checkpoint shards: 40%" }])).toEqual(["other"]);
  });
});

describe("fromLokiStreams", () => {
  it("reads a Serve line's JSON, names each stream, and puts the oldest first", () => {
    const lines = fromLokiStreams([
      {
        labels: { job: "ray-serve", source: "engine", host: "192.0.2.71" },
        entries: [{ ts: "2026-09-30T06:44:12Z", line: "(EngineCore pid=1) ValueError: no" }],
      },
      {
        labels: { job: "ray-serve", host: "192.0.2.49", level: "unknown" },
        entries: [{ ts: "2026-09-30T06:44:00Z", line: '{"levelname": "INFO", "message": "Started"}' }],
      },
      { labels: { job: "node-agent", host: "192.0.2.49" }, entries: [{ ts: "2026-09-30T06:44:05Z", line: "heartbeat" }] },
    ]);
    expect(lines.map((line) => [line.source, line.host, line.level ?? null, line.message])).toEqual([
      ["replica", "192.0.2.49", "INFO", "Started"],
      ["agent", "192.0.2.49", null, "heartbeat"],
      ["engine", "192.0.2.71", null, "(EngineCore pid=1) ValueError: no"],
    ]);
  });
});

describe("fromText", () => {
  it("keeps a leading timestamp as the line's time and drops blank lines", () => {
    expect(fromText("2026-09-30T06:44:00.123Z ready\n\nplain line\n")).toEqual([
      { ts: "2026-09-30T06:44:00.123Z", message: "ready", source: undefined },
      { message: "plain line", source: undefined },
    ]);
  });
});

describe("LogViewer", () => {
  it("colours each row by its level and counts each level", () => {
    render(<LogViewer lines={FAILED_START} />);
    const viewer = screen.getByTestId("log-viewer");
    expect(viewer.querySelectorAll('[data-level="error"]')).toHaveLength(3);
    expect(screen.getByTestId("log-level-error")).toHaveTextContent("Errors 3");
    expect(screen.getByTestId("log-level-info")).toHaveTextContent("Info 1");
  });

  it("hides a level when its chip is clicked", async () => {
    render(<LogViewer lines={FAILED_START} />);
    await userEvent.click(screen.getByTestId("log-level-info"));
    expect(screen.queryByText("Started LLMDeployment:qwen")).not.toBeInTheDocument();
    expect(screen.getByText(/ngram_embedding\.weight_scale/)).toBeInTheDocument();
  });

  it("names the streams and filters by them when there is more than one", async () => {
    render(<LogViewer lines={FAILED_START} />);
    await userEvent.click(screen.getByRole("button", { name: "Engine (vLLM)" }));
    expect(screen.getByText("Started LLMDeployment:qwen")).toBeInTheDocument();
    expect(screen.queryByText(/ngram_embedding/)).not.toBeInTheDocument();
  });

  it("searches the lines and says when nothing matches", async () => {
    render(<LogViewer lines={FAILED_START} />);
    const search = screen.getByPlaceholderText("Search the lines");
    await userEvent.type(search, "weight_scale");
    expect(screen.getByTestId("log-viewer").querySelectorAll(".row")).toHaveLength(1);
    expect(screen.getByTestId("log-viewer").querySelector("mark")).toHaveTextContent("weight_scale");
    await userEvent.type(search, "-not-there");
    expect(screen.getByText("No line matches the filters.")).toBeInTheDocument();
  });

  it("says why it is empty", () => {
    render(<LogViewer lines={[]} empty="could not reach the node: timeout" />);
    expect(within(screen.getByTestId("log-viewer")).getByText("could not reach the node: timeout")).toBeInTheDocument();
  });
});
