/**
 * The budgets API as the server serves it (B5): the addresses and bodies, amounts that come as decimal
 * strings, the fields a write was refused for, the chargeback CSV address, and the form's own rules for
 * amounts and thresholds.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BudgetProblemsError,
  budgetsApi,
  currencyOk,
  parseAmount,
  parseThresholds,
  readMyBudgets,
} from "./budgets";
import { ApiError } from "./http";

const fetchMock = vi.fn();

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("budgets API", () => {
  it("lists the budgets and reads decimal amounts as numbers", async () => {
    fetchMock.mockImplementation(async () =>
      answer(200, [
        {
          id: "b-1",
          name: "Sales",
          scope: "team",
          target_id: "g-1",
          target_label: "Sales",
          unit: "money",
          currency: "EUR",
          amount: "1000.0000",
          period: "month",
          mode: "soft",
          thresholds: [80, 100],
          owner_id: null,
          period_start: "2026-10-01",
          period_end: "2026-10-31",
          spent: "250.5000",
          reserved: null,
          percent: 25.05,
          forecast_end: null,
          would_refuse: 0,
          children_exceed: true,
          suppressed: false,
        },
      ]),
    );

    const [first] = await budgetsApi.list();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/ee/budgets");
    expect(first).toMatchObject({ amount: 1000, spent: 250.5, percent: 25.05, children_exceed: true, suppressed: false });
  });

  it("creates, changes only the fields given, and deletes", async () => {
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) =>
      init.method === "DELETE" ? new Response(null, { status: 204 }) : answer(200, { id: "b 1", amount: 5 }),
    );

    await budgetsApi.create({
      name: "Org",
      scope: "tenant",
      target_id: null,
      unit: "tokens",
      currency: null,
      amount: 5000000,
      period: "week",
      mode: "shadow",
      thresholds: [50],
      owner_id: null,
    });
    await budgetsApi.update("b 1", { amount: 6000000 });
    await budgetsApi.remove("b 1");

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ scope: "tenant", target_id: null, amount: 5000000 });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/ee/budgets/b%201");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "PATCH", body: '{"amount":6000000}' });
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "DELETE" });
  });

  it("carries the fields a write was refused for", async () => {
    fetchMock.mockImplementation(async () =>
      answer(422, { detail: { problems: [{ field: "target_id", code: "team_too_small" }] } }),
    );

    const err = await budgetsApi.update("b-1", { amount: 1 }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(BudgetProblemsError);
    expect((err as BudgetProblemsError).problems).toEqual([{ field: "target_id", code: "team_too_small" }]);
  });

  it("carries a refusal's code (root mode for the person budgets switch)", async () => {
    fetchMock.mockImplementation(async () => answer(403, { detail: { code: "root_mode", message: "root mode" } }));

    const err = await budgetsApi.updateSettings({ person_budgets: true }).catch((e: unknown) => e);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/ee/budgets/settings");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PUT", body: '{"person_budgets":true}' });
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("root_mode");
  });

  it("asks for the chargeback report, and names its CSV", async () => {
    fetchMock.mockImplementation(async () => answer(200, { month: "2026-09", by: "cost_center", min_group_size: 7, rows: [] }));

    const report = await budgetsApi.chargeback("2026-09", "cost_center");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/ee/budgets/chargeback?month=2026-09&by=cost_center&format=json");
    expect(report).toMatchObject({ by: "cost_center", min_group_size: 7, rows: [] });
    expect(budgetsApi.chargebackCsvUrl("2026-09", "team")).toBe(
      "/api/ee/budgets/chargeback?month=2026-09&by=team&format=csv",
    );
  });
});

describe("the form's rules", () => {
  it("takes whole tokens, and money with up to four decimals (point or comma)", () => {
    expect(parseAmount("1000000", "tokens")).toBe(1000000);
    expect(parseAmount("1 000 000", "tokens")).toBe(1000000);
    expect(parseAmount("12.5", "tokens")).toBeNull();
    expect(parseAmount("0", "tokens")).toBeNull();
    expect(parseAmount("249,99", "money")).toBe(249.99);
    expect(parseAmount("10.12345", "money")).toBeNull();
    expect(parseAmount("-5", "money")).toBeNull();
  });

  it("reads thresholds as whole percentages, each once, in order", () => {
    expect(parseThresholds("100, 80")).toEqual([80, 100]);
    expect(parseThresholds("50 % 80% 80")).toEqual([50, 80]);
    expect(parseThresholds("")).toEqual([]);
    expect(parseThresholds("0")).toBeNull();
    expect(parseThresholds("80, lots")).toBeNull();
    expect(parseThresholds("1001")).toBeNull();
  });

  it("takes three-letter currencies", () => {
    expect(currencyOk("EUR")).toBe(true);
    expect(currencyOk("eur")).toBe(true);
    expect(currencyOk("EURO")).toBe(false);
  });
});

describe("the budgets on My usage", () => {
  it("reads the block defensively", () => {
    const read = readMyBudgets({
      budgets: [{ name: "Mine", kind: "personal", unit: "money", currency: "EUR", amount: "20.0000", spent: "5", percent: 25, period_end: "2026-10-31", mode: "soft" }, null],
      alerts: [{ name: "Mine", threshold: 80, at: "2026-10-20" }],
    });
    expect(read.budgets).toEqual([
      { name: "Mine", kind: "personal", unit: "money", currency: "EUR", amount: 20, spent: 5, percent: 25, period_end: "2026-10-31", mode: "soft" },
    ]);
    expect(read.alerts).toEqual([{ name: "Mine", threshold: 80, at: "2026-10-20" }]);
    expect(readMyBudgets(undefined)).toEqual({ budgets: [], alerts: [] });
  });
});
