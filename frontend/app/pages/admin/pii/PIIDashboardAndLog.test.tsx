/**
 * PII Dashboard and Activity Log speed/duration display tests.
 */
import { screen } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as pii from "~/api/pii";
import { renderPage } from "~/test/renderPage";
import { getAppTheme } from "~/theme";

import PIIActivityLogPage from "./PIIActivityLogPage";
import PIIDashboardPage from "./PIIDashboardPage";

vi.mock("~/api/pii", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/pii")>();
  return {
    ...actual,
    fetchPIIStats: vi.fn(),
    fetchPIIEvents: vi.fn(),
  };
});

const api = vi.mocked(pii);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PIIDashboardPage speed tiles", () => {
  it("shows average, p50, p95, and max processing times", async () => {
    api.fetchPIIStats.mockResolvedValue({
      total_scans: 120,
      total_with_pii: 45,
      total_entities: 98,
      detection_rate: 0.375,
      avg_duration_ms: 48.2,
      p50_duration_ms: 41.0,
      p95_duration_ms: 112.4,
      max_duration_ms: 285.0,
      entity_type_breakdown: { PERSON: 60, EMAIL_ADDRESS: 38 },
      operation_breakdown: { sanitize: 120 },
      source_breakdown: { gateway: 120 },
      daily_volume: [],
    });

    renderPage(
      <ThemeProvider theme={getAppTheme("light")}>
        <PIIDashboardPage />
      </ThemeProvider>,
    );
    expect(await screen.findByText("Avg Processing Time")).toBeInTheDocument();
    expect(screen.getByText("48 ms")).toBeInTheDocument();
    expect(screen.getByText("p50 41 ms")).toBeInTheDocument();
    expect(screen.getByText("p95 Processing Time")).toBeInTheDocument();
    expect(screen.getByText("112 ms")).toBeInTheDocument();
    expect(screen.getByText("max 285 ms")).toBeInTheDocument();
  });
});

describe("PIIActivityLogPage per-request duration column", () => {
  it("shows formatted duration for each event and em-dash when absent", async () => {
    api.fetchPIIEvents.mockResolvedValue({
      total: 2,
      items: [
        {
          id: "ev-1",
          created_at: "2026-10-20T08:00:00Z",
          operation: "sanitize",
          mode: "redact",
          language: "en",
          score_threshold: 0.35,
          pii_detected: true,
          entities_found: 2,
          entity_type_counts: { PERSON: 1, EMAIL_ADDRESS: 1 },
          source: "gateway",
          request_id: "req-1",
          duration_ms: 64.3,
        },
        {
          id: "ev-2",
          created_at: "2026-10-20T08:01:00Z",
          operation: "scan",
          mode: null,
          language: "de",
          score_threshold: 0.35,
          pii_detected: false,
          entities_found: 0,
          entity_type_counts: null,
          source: "api",
          request_id: null,
          duration_ms: null,
        },
      ],
    });

    renderPage(<PIIActivityLogPage />);
    expect(await screen.findByText("Duration")).toBeInTheDocument();
    expect(screen.getByText("64 ms")).toBeInTheDocument();
  });
});
