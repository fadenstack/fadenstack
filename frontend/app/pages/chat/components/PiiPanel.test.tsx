/**
 * The chat's Privacy panel shows the mode the chat's requests get. A mode the
 * chat asked for but does not get is shown as not applied, with the reason --
 * it used to show as active while the turns were redacted.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { normalizePIIPolicy, type SessionPIIPolicy } from "~/api/pii";
import { applyOverride } from "../hooks/useSessionPiiPolicy";

import PiiPanel from "./PiiPanel";

const FLOOR = normalizePIIPolicy({ egress: { enabled_for_cloud: true, mode: "redact", fail_action: "block" } });

function policy(overrides: Partial<SessionPIIPolicy> = {}): SessionPIIPolicy {
  return {
    session_id: "s-1",
    has_override: false,
    override: null,
    floor: FLOOR,
    effective: FLOOR,
    mode_switch_allowed: false,
    can_switch_mode: false,
    mode_applied: true,
    ...overrides,
  };
}

function show(value: SessionPIIPolicy) {
  render(
    <PiiPanel
      policy={value}
      loading={false}
      error={null}
      onUpdate={vi.fn().mockResolvedValue(undefined)}
      onClear={vi.fn().mockResolvedValue(undefined)}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  return screen.getByRole("combobox", { name: /^Mode/ });
}

describe("PiiPanel", () => {
  it("shows a mode the admin has not allowed as not applied, and why", () => {
    const mode = show(
      policy({ has_override: true, override: { egress_mode: "tokenize_reversible" }, mode_applied: false }),
    );
    expect(mode).toHaveTextContent("redact");
    expect(mode).toHaveAttribute("aria-disabled", "true");
    const note = screen.getByTestId("pii-mode-not-applied");
    expect(note).toHaveTextContent("tokenize_reversible is not applied.");
    expect(note).toHaveTextContent("Your admin hasn't allowed changing the PII mode for this chat.");
  });

  it("says the mode is locked even before anything was chosen", () => {
    const mode = show(policy());
    expect(mode).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Your admin hasn't allowed changing the PII mode for this chat.")).toBeInTheDocument();
    expect(screen.queryByTestId("pii-mode-not-applied")).not.toBeInTheDocument();
  });

  it("asks an admin to choose a mode again that was saved without the right", () => {
    const mode = show(
      policy({
        has_override: true,
        override: { egress_mode: "tokenize_reversible" },
        can_switch_mode: true,
        mode_applied: false,
      }),
    );
    expect(mode).not.toHaveAttribute("aria-disabled");
    expect(screen.getByTestId("pii-mode-not-applied")).toHaveTextContent("Choose it again to apply it.");
  });

  it("shows an applied mode as the mode, with no note", () => {
    const tokenized = { ...FLOOR, egress: { ...FLOOR.egress, mode: "tokenize_reversible" as const } };
    const mode = show(
      policy({
        has_override: true,
        override: { egress_mode: "tokenize_reversible" },
        effective: tokenized,
        mode_switch_allowed: true,
        can_switch_mode: true,
      }),
    );
    expect(mode).toHaveTextContent("tokenize_reversible");
    expect(screen.queryByTestId("pii-mode-not-applied")).not.toBeInTheDocument();
    expect(screen.queryByText("Your admin hasn't allowed changing the PII mode for this chat.")).not.toBeInTheDocument();
  });
});

describe("a new chat's local choice", () => {
  it("changes the mode only when it may", () => {
    expect(applyOverride(FLOOR, { egress_mode: "tokenize_reversible" }, false).egress.mode).toBe("redact");
    expect(applyOverride(FLOOR, { egress_mode: "tokenize_reversible" }, true).egress.mode).toBe("tokenize_reversible");
  });
});

describe("the score threshold", () => {
  const withThreshold = normalizePIIPolicy({
    egress: { enabled_for_cloud: true, mode: "redact", fail_action: "block" },
    presidio: { threshold: 0.5, entities: ["EMAIL_ADDRESS"] },
  });

  it("only goes down from the policy's value without the weaken right", () => {
    show(policy({ floor: withThreshold, effective: withThreshold }));
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuemax", "0.5");
    expect(slider).toHaveAttribute("aria-valuemin", "0.01");
  });

  it("may go anywhere with the weaken right", () => {
    show(policy({ floor: withThreshold, effective: withThreshold, can_weaken: true }));
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuemax", "1");
    expect(slider).toHaveAttribute("aria-valuemin", "0");
  });

  it("is applied locally as the stricter value unless weakening is allowed", () => {
    expect(applyOverride(withThreshold, { presidio_threshold: 1 }, false, false).presidio.threshold).toBe(0.5);
    expect(applyOverride(withThreshold, { presidio_threshold: 0.2 }, false, false).presidio.threshold).toBe(0.2);
    expect(applyOverride(withThreshold, { presidio_threshold: 1 }, false, true).presidio.threshold).toBe(1);
  });
});
