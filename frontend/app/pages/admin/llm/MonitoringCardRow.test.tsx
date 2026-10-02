/**
 * The way to a provider's Grafana dashboard is a link that looks like one.
 *
 * It was a hint in the muted colour of a disabled control, and read as
 * unavailable while the dashboard was there (.61, 2026-10-01).
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { providers, type Provider } from "~/api/llm";

import MonitoringCardRow from "./MonitoringCardRow";

const DASHBOARD = "/grafana/d/vllm-rt-a53fe3c1/vllm-runtime?orgId=1&var-model=Qwen3.8-27B-FP8";

describe("MonitoringCardRow", () => {
  beforeEach(() => {
    vi.spyOn(providers, "monitoringStats").mockResolvedValue({
      enabled: true,
      stale: false,
      dashboard_url: DASHBOARD,
      stats: { running_requests: 0, generation_tokens_per_sec: 12.5 },
    } as never);
  });

  it("links to the dashboard, opening it in a new tab", async () => {
    render(<MonitoringCardRow provider={{ id: "p1", name: "qwen" } as unknown as Provider} />);
    const link = await screen.findByTestId("monitoring-dashboard-link");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", DASHBOARD);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).not.toHaveClass("Mui-disabled");
  });
});
