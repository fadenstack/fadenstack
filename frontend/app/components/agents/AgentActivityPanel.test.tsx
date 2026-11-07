/**
 * An agent's activity names nobody but the viewer: figures about fewer than k people are hidden (and a day
 * about fewer is a gap), and another person's event shows no name (M02, M04).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentsApi, type AgentUsage, type ClientEvent } from "~/api/agents";
import { getAppTheme } from "~/theme";
import { agentFixture } from "~/test/agentFixtures";

import { AgentActivityPanel } from "./AgentActivityPanel";

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: { ...actual.agentsApi, usage: vi.fn(), events: vi.fn(), signIns: vi.fn(), namePrincipals: vi.fn() },
  };
});

// The chart is MUI's; what goes into it is the panel's (a gap is null, never 0).
vi.mock("~/components/dashboard/TimeSeriesChart", () => ({
  default: ({ ariaLabel, series }: { ariaLabel: string; series: { id: string; data: (number | null)[] }[] }) => (
    <div role="img" aria-label={ariaLabel} data-series={JSON.stringify(series.map((s) => s.data))} />
  ),
}));

const api = vi.mocked(agentsApi);

function show() {
  return render(
    <ThemeProvider theme={getAppTheme("light")}>
      <AgentActivityPanel agent={agentFixture()} />
    </ThemeProvider>,
  );
}

function usage(overrides: Partial<AgentUsage> = {}): AgentUsage {
  return {
    suppressed: false,
    min_group_size: 5,
    requests: 40,
    errors: 1,
    prompt_tokens: 1000,
    completion_tokens: 500,
    total_tokens: 1500,
    users: 6,
    conversations: 9,
    by_day: [],
    ...overrides,
  };
}

function event(overrides: Partial<ClientEvent> = {}): ClientEvent {
  return {
    id: "e-1",
    type: "tool.executed",
    user_id: null,
    device_id: null,
    conversation_id: null,
    data: { tool: "read_range", outcome: "ok" },
    occurred_at: "2026-10-28T10:00:00Z",
    created_at: "2026-10-28T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.usage.mockResolvedValue(usage());
  api.events.mockResolvedValue([]);
  api.signIns.mockResolvedValue([]);
  api.namePrincipals.mockResolvedValue([{ type: "user", id: "u-me", label: "me@plant.example" }]);
});

describe("an agent's activity", () => {
  it("hides the figures when fewer than k people used the agent", async () => {
    api.usage.mockResolvedValue(
      usage({ suppressed: true, requests: null, errors: null, prompt_tokens: null, completion_tokens: null, total_tokens: null, users: null, conversations: null }),
    );
    const { container } = show();

    await waitFor(() => expect(container.querySelector("[data-agent-usage-suppressed]")).not.toBeNull());
    expect(container.querySelector("[data-agent-usage-suppressed]")!.textContent).toMatch(
      /Fewer than 5 people used this agent in this period/,
    );
    expect(screen.queryByText("Users")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("leaves a day about fewer than k people out of the chart, as a gap", async () => {
    api.usage.mockResolvedValue(
      usage({
        by_day: [
          { day: "2026-10-27", requests: 30, total_tokens: 900, suppressed: false },
          { day: "2026-10-28", requests: null, total_tokens: null, suppressed: true },
        ],
      }),
    );
    show();

    const chart = await screen.findByRole("img");
    expect(JSON.parse(chart.getAttribute("data-series")!)).toEqual([[30, null]]);
    expect(screen.getByText("Days with fewer than 5 people are left out.")).toBeInTheDocument();
  });

  it("names the viewer's own events and nobody else's, and searches either", async () => {
    api.events.mockResolvedValue([
      event({ id: "e-mine", user_id: "u-me" }),
      event({ id: "e-other", user_id: null, data: { tool: "write_range", outcome: "ok" } }),
    ]);
    show();

    expect(await screen.findByText("me@plant.example")).toBeInTheDocument();
    expect(screen.getByText("Not shown")).toBeInTheDocument();
    expect(api.namePrincipals).toHaveBeenCalledWith([{ type: "user", id: "u-me" }]);

    // A row without a name must not break the search.
    await userEvent.type(screen.getAllByPlaceholderText(/Search/)[0], "write_range");
    await waitFor(() => expect(screen.queryByText("me@plant.example")).toBeNull());
    expect(screen.getByText("Not shown")).toBeInTheDocument();
  });
});
