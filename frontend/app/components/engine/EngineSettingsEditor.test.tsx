import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { EngineConfig } from "~/lib/engine";

import { EngineSettingsEditor, type EngineSuggestion, type MacEngineView } from "./EngineSettingsEditor";

const SUGGESTED: EngineSuggestion = {
  config: { enable_auto_tool_choice: true, tool_call_parser: "hermes", reasoning_parser: "qwen3", max_model_len: 32768 },
  reasons: { tool_call_parser: "model_family", reasoning_parser: "model_family", max_model_len: "default_cap" },
};

function Harness({
  initial = {} as EngineConfig,
  onValue,
  isSuperuser = true,
  mac,
}: {
  mac?: MacEngineView;
  initial?: EngineConfig;
  onValue?: (v: EngineConfig) => void;
  isSuperuser?: boolean;
}) {
  const [value, setValue] = useState<EngineConfig>(initial);
  const [extra, setExtra] = useState("");
  return (
    <EngineSettingsEditor
      value={value}
      extra={extra}
      target="cluster"
      model={{ repoId: "Qwen/Qwen3-8B", maxContext: 40960, capabilities: ["tools", "reasoning"], kvBytesPerToken: 147456 }}
      hardware={{ gpuBytes: 130_663_055_360, tensorParallel: 1, maxContextThatFits: 40960 }}
      suggested={SUGGESTED}
      isSuperuser={isSuperuser}
      mac={mac}
      onChange={(v, e) => {
        setValue(v);
        setExtra(e);
        onValue?.(v);
      }}
    />
  );
}

describe("EngineSettingsEditor", () => {
  it("shows the suggested settings in words, and the command they make", () => {
    render(<Harness initial={{ ...SUGGESTED.config }} />);
    expect(screen.getByText("Tool calling")).toBeInTheDocument();
    expect(screen.getByLabelText("Tool calling")).toBeChecked();
    expect(screen.getByTestId("engine-preview")).toHaveTextContent(
      "vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 --max-model-len 32768",
    );
    // The cost of the chosen context, from the model's own KV size.
    expect(screen.getByText(/One conversation this long takes about 4\.8 GB/)).toBeInTheDocument();
  });

  it("turning tools off removes both settings", async () => {
    let last: EngineConfig = {};
    render(<Harness initial={{ ...SUGGESTED.config }} onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByLabelText("Tool calling"));
    expect(last.enable_auto_tool_choice).toBeUndefined();
    expect(last.tool_call_parser).toBeUndefined();
    expect(last.reasoning_parser).toBe("qwen3");
  });

  it("a preset changes what it is for and says so", async () => {
    let last: EngineConfig = {};
    render(<Harness initial={{ ...SUGGESTED.config }} onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByTestId("engine-preset-many_users"));
    expect(last).toMatchObject({ max_num_seqs: 256, enable_prefix_caching: true, tool_call_parser: "hermes" });
  });

  it("warns before a setting that will not start", async () => {
    render(<Harness initial={{ max_model_len: 65536 }} />);
    expect(screen.getByTestId("engine-warning-context_over_model")).toHaveTextContent("40K");
  });

  it("every vLLM flag is reachable by search", async () => {
    let last: EngineConfig = {};
    render(<Harness onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByText(/All vLLM settings/));
    fireEvent.change(screen.getByPlaceholderText(/Search by flag/), { target: { value: "async-scheduling" } });
    const row = await screen.findByTestId("engine-flag-async-scheduling");
    await userEvent.click(within(row).getByRole("combobox"));
    await userEvent.click(screen.getByRole("option", { name: "On" }));
    expect(last).toEqual({ async_scheduling: true });
  });

  it("shows an operator a superuser-only setting as it is, locked, with the reason", async () => {
    render(<Harness initial={{ trust_remote_code: true }} isSuperuser={false} />);
    const toggle = screen.getByLabelText("Run the model's own code");
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getAllByText("Only an administrator can change this.").length).toBeGreaterThan(0);
  });

  it("locks the same setting in the all-flags list for an operator", async () => {
    render(<Harness initial={{ scheduler_cls: "a.B" }} isSuperuser={false} />);
    await userEvent.click(screen.getByText(/All vLLM settings/));
    const row = await screen.findByTestId("engine-flag-scheduler-cls");
    expect(within(row).getByRole("textbox")).toBeDisabled();
    expect(within(row).getByText("Only an administrator can change this.")).toBeInTheDocument();
  });

  it("lets a superuser change it", async () => {
    let last: EngineConfig = {};
    render(<Harness isSuperuser onValue={(v) => (last = v)} />);
    const toggle = screen.getByLabelText("Run the model's own code");
    expect(toggle).toBeEnabled();
    expect(screen.queryByText("Only an administrator can change this.")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(last).toEqual({ trust_remote_code: true });
  });
});

// The backend's `metal_policy.form_policy()`; its own test pins the list to what the refusal accepts.
const MAC: MacEngineView = {
  policy: {
    allowed_keys: [
      "max_model_len", "max_num_seqs", "max_num_batched_tokens", "gpu_memory_utilization", "dtype", "seed",
      "generation_config", "quantization", "enable_auto_tool_choice", "tool_call_parser", "reasoning_parser",
      "enable_prefix_caching",
    ],
    forced: { enable_prefix_caching: false },
    tool_parsers: ["hermes"],
    reasoning_parsers: ["qwen3"],
  },
  workingSetBytes: 55_662_788_608,
  runtimeVersion: "0.34.0",
};

describe("EngineSettingsEditor for a Mac", () => {
  it("shows what a Mac refuses off, each with its reason", () => {
    render(<Harness mac={MAC} />);
    expect(screen.getByLabelText("8-bit context cache")).toBeDisabled();
    expect(screen.getByText(/vLLM-Metal does not support an 8-bit context cache/)).toBeInTheDocument();
    expect(screen.getByLabelText("Skip graph capture")).toBeDisabled();
    expect(screen.getByLabelText("Run the model's own code")).toBeDisabled();
    expect(screen.getByText("Prefix caching stays off on a Mac.")).toBeInTheDocument();
    // One muted line with a lock; the setting's long help gives way to it.
    expect(screen.getAllByTestId("engine-refused").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Split long prompts").length).toBeGreaterThan(0);
    // The three presets that need a refused setting; Balanced stays.
    for (const id of ["long_context", "many_users", "low_memory"]) expect(screen.getByTestId(`engine-preset-${id}`)).toBeDisabled();
    expect(screen.getByTestId("engine-preset-balanced")).toBeEnabled();
  });

  it("says the share is a fraction of what the Mac offers, and lists the Mac runtime's settings", async () => {
    render(<Harness mac={MAC} initial={{ gpu_memory_utilization: 0.5 }} />);
    expect(screen.getByText(/Up to 27\.8 GB of the 55\.7 GB the Mac offers/)).toBeInTheDocument();
    expect(screen.getByText(/Settings the Mac allows \(\d+, vLLM-Metal 0\.34\.0\)/)).toBeInTheDocument();
    expect(screen.queryByText(/All vLLM settings/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/Settings the Mac allows/));
    await userEvent.type(screen.getByPlaceholderText(/Search by flag/), "eager");
    expect(screen.queryByTestId("engine-flag-enforce-eager")).not.toBeInTheDocument();
    await userEvent.clear(screen.getByPlaceholderText(/Search by flag/));
    await userEvent.type(screen.getByPlaceholderText(/Search by flag/), "dtype");
    expect(screen.getByTestId("engine-flag-dtype")).toBeInTheDocument();
  });

  it("offers only the parsers tested on a Mac", async () => {
    render(<Harness mac={MAC} initial={{ enable_auto_tool_choice: true, tool_call_parser: "hermes" }} />);
    await userEvent.click(screen.getByRole("combobox", { name: "Format" }));
    expect(screen.getAllByRole("option").map((o) => o.textContent?.replace(/ · .*/, ""))).toEqual(["hermes"]);
  });

  it("has a sentence in every language for each setting the form can refuse", async () => {
    const refused = ["kv_cache_dtype", "enable_prefix_caching", "enable_chunked_prefill", "enforce_eager", "trust_remote_code"];
    for (const l of ["en", "de", "es", "fr", "zh"]) {
      const { default: common } = await import(`../../../../backend/i18n/${l}/common.json`);
      for (const key of [...refused, "preset", "parsers"]) expect(common.engine.mac[key], `${l} ${key}`).toBeTruthy();
      expect(common.engine.memory.help_mac).toBeTruthy();
    }
  });
});

describe("EngineSettingsEditor for Ray", () => {
  it("is unchanged: nothing is refused and the share is of each card", () => {
    render(<Harness initial={{ gpu_memory_utilization: 0.5 }} />);
    expect(screen.getByLabelText("8-bit context cache")).toBeEnabled();
    expect(screen.getByLabelText("Skip graph capture")).toBeEnabled();
    for (const id of ["balanced", "long_context", "many_users", "low_memory"]) expect(screen.getByTestId(`engine-preset-${id}`)).toBeEnabled();
    expect(screen.getByText(/Up to .* of each .* card/)).toBeInTheDocument();
    expect(screen.getByText(/All vLLM settings \(\d+, vLLM /)).toBeInTheDocument();
  });
});
