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
      ...value, presidio: { ...value.presidio, entities: ["PERSON"], profile_id: options.profile_id, threshold_mode: "profile", threshold: .35 },
    });
  });

  it("cannot adopt defaults from an unavailable service", () => {
    render(<PiiPolicyForm value={normalizePIIPolicy(null)} options={{ ...options, ready: false }} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Use installed profile defaults" })).toBeDisabled();
  });

  const patronus: PIIPolicyOptionsResponse = {
    ...options, profile_id: "patronus-small-pii", supported_languages: ["en"], default_language: "en",
    supported_entities: ["PERSON", "EMAIL_ADDRESS"],
    entity_languages: { en: ["PERSON", "EMAIL_ADDRESS"] },
    default_entities: { en: ["PERSON", "EMAIL_ADDRESS"] },
  };
  const policy = (entities: string[], mode?: "legacy" | "profile") => normalizePIIPolicy({
    presidio: { entities, language: "en", threshold: .5, ...(mode ? { threshold_mode: mode } : {}) },
  });

  it("removes types the installed detector does not find when adopting its profile", () => {
    const value = policy(["PERSON", "NRP"], "legacy");
    const change = vi.fn();
    render(<PiiPolicyForm value={value} options={patronus} onChange={change} />);
    fireEvent.click(screen.getByRole("button", { name: "Use installed profile defaults" }));
    const next = change.mock.calls[0][0];
    expect(next.presidio.entities).toEqual(["PERSON"]);
    expect(next.presidio.threshold_mode).toBe("profile");
    expect(next.presidio.profile_id).toBe("patronus-small-pii");
  });

  it("warns about a legacy threshold for a local detector only", () => {
    const value = policy(["PERSON"], "legacy");
    const { unmount } = render(<PiiPolicyForm value={value} options={patronus} onChange={vi.fn()} />);
    expect(screen.getByText(/scale of the older engine/)).toBeInTheDocument();
    unmount();
    render(<PiiPolicyForm value={value} options={{ ...patronus, profile_id: "presidio-legacy" }} onChange={vi.fn()} />);
    expect(screen.queryByText(/scale of the older engine/)).not.toBeInTheDocument();
  });

  it("does not warn about the threshold when the policy uses profile thresholds", () => {
    render(<PiiPolicyForm value={policy(["PERSON"], "profile")} options={patronus} onChange={vi.fn()} />);
    expect(screen.queryByText(/scale of the older engine/)).not.toBeInTheDocument();
  });

  it("does not warn about a threshold of its own that names the installed profile (the detector takes it)", () => {
    const value = normalizePIIPolicy({
      presidio: { entities: ["PERSON"], language: "en", threshold: .5, threshold_mode: "override", profile_id: "patronus-small-pii" },
    });
    render(<PiiPolicyForm value={value} options={patronus} onChange={vi.fn()} />);
    expect(screen.queryByText(/scale of the older engine/)).not.toBeInTheDocument();
  });

  it("names the types the installed detector does not find", () => {
    render(<PiiPolicyForm value={policy(["PERSON", "NRP"], "profile")} options={patronus} onChange={vi.fn()} />);
    expect(screen.getByText(/does not find NRP/)).toBeInTheDocument();
  });

  it("shows the standard types when no entity is selected", () => {
    const { unmount } = render(<PiiPolicyForm value={policy([], "profile")} options={patronus} onChange={vi.fn()} />);
    expect(screen.getByText(/standard types are found: PERSON, EMAIL_ADDRESS/)).toBeInTheDocument();
    unmount();
    render(<PiiPolicyForm value={policy([], "profile")} options={{ ...patronus, default_entities: undefined }} onChange={vi.fn()} />);
    expect(screen.getByText(/PII service's standard types are found/)).toBeInTheDocument();
  });
});
