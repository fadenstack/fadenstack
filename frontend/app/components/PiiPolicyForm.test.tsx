import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { normalizePIIPolicy, type PIIPolicyOptionsResponse } from "~/api/pii";
import PiiPolicyForm from "./PiiPolicyForm";

const options: PIIPolicyOptionsResponse = {
  source: "pii_service", ready: true, profile_id: "patronus-small-pii:german-v1",
  supported_entities: ["PERSON"], supported_languages: ["de"], supported_sanitize_modes: ["redact", "tokenize"],
  telemetry_modes: ["sanitized"], egress_modes: ["redact"], fail_actions: ["block"],
  default_language: "de", default_score_threshold: .35,
  detector: { model_id: "patronus-onnx-v1", locale_pack: "german-v1", execution: "cpu" },
};

describe("local detector adoption", () => {
  it("shows the installed locale and preserves protection choices when adopting its profile", () => {
    const value = normalizePIIPolicy({
      egress: { enabled_for_cloud: true, enabled_for_local: true, fail_action: "block" },
      presidio: { entities: ["PERSON", "CUSTOM_ID"], language: "de", threshold: .8 },
    });
    const change = vi.fn();
    render(<PiiPolicyForm value={value} options={options} onChange={change} />);
    expect(screen.getByText(/patronus-onnx-v1 · german-v1 · cpu/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use installed profile defaults" }));
    expect(change).toHaveBeenCalledWith({
      ...value, presidio: { ...value.presidio, profile_id: options.profile_id, threshold_mode: "profile", threshold: .35 },
    });
  });

  it("cannot adopt defaults from an unavailable service", () => {
    render(<PiiPolicyForm value={normalizePIIPolicy(null)} options={{ ...options, ready: false }} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Use installed profile defaults" })).toBeDisabled();
  });
});
