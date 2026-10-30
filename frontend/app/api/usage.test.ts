/**
 * The usage API as the backend serves it (C5): the addresses and parameters,
 * and the refusals the console words -- a code, or the fields a settings
 * write was refused for.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./http";
import { attachmentName, REASON_HEADER, reasonOk, usageApi, UsageProblemsError } from "./usage";

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

describe("usage API", () => {
  it("asks for the own usage and the admin view with the contract's parameters", async () => {
    fetchMock.mockImplementation(async () => answer(200, {}));

    await usageApi.me({ from: "2026-10-01", to: "2026-10-28", bucket: "month" });
    await usageApi.admin({ from: "2026-10-01", to: "2026-10-28", bucket: "hour", group_by: "provider" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/usage/me?from=2026-10-01&to=2026-10-28&bucket=month");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/admin/usage?from=2026-10-01&to=2026-10-28&bucket=hour&group_by=provider",
    );
  });

  it("sends a reason as a header, percent-encoded, never in the address (ASVS 14.2.1)", async () => {
    fetchMock.mockImplementation(async () => answer(200, { granularity: "day", rows: [] }));

    await usageApi.people({ from: "2026-10-01", to: "2026-10-28", team: "g-1", reason: "Prüfung Q4: Kosten & Nutzung" });
    await usageApi.admin({ from: "2026-10-01", to: "2026-10-28", reason: "Cost dispute of the pilot" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/usage/people?from=2026-10-01&to=2026-10-28&team=g-1");
    expect(fetchMock.mock.calls[0][1].headers[REASON_HEADER]).toBe("Pr%C3%BCfung%20Q4%3A%20Kosten%20%26%20Nutzung");
    expect(decodeURIComponent(fetchMock.mock.calls[0][1].headers[REASON_HEADER])).toBe("Prüfung Q4: Kosten & Nutzung");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/admin/usage?from=2026-10-01&to=2026-10-28");
    expect(fetchMock.mock.calls[1][1].headers[REASON_HEADER]).toBe("Cost%20dispute%20of%20the%20pilot");
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).not.toContainEqual(expect.stringContaining("reason"));
  });

  it("sends no reason header without a reason", async () => {
    fetchMock.mockImplementation(async () => answer(200, {}));

    await usageApi.admin({ bucket: "day" });

    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty(REASON_HEADER);
  });

  it("asks whom the per-person view may be about", async () => {
    fetchMock.mockImplementation(async () => answer(200, { people: [{ id: "u-1", email: "pat@example.com" }], teams: [] }));

    const found = await usageApi.peopleChoices();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/usage/people/choices");
    expect(found.people).toEqual([{ id: "u-1", email: "pat@example.com" }]);
  });

  it("fetches one person's data with the reason as a header, named as the server says", async () => {
    fetchMock.mockImplementation(
      async () =>
        new Response("period,requests", {
          status: 200,
          headers: { "content-type": "text/csv", "content-disposition": 'attachment; filename="usage-u 1.csv"' },
        }),
    );

    const file = await usageApi.peopleExport("u 1", "csv", "Access request by the person");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/usage/people/u%201/export?format=csv");
    expect(fetchMock.mock.calls[0][1].headers[REASON_HEADER]).toBe("Access%20request%20by%20the%20person");
    expect(file.filename).toBe("usage-u 1.csv");
    expect(await file.blob.text()).toBe("period,requests");
  });

  it("carries a refused download's code", async () => {
    fetchMock.mockImplementation(async () => answer(403, { detail: { code: "person_reporting_off" } }));

    const err = await usageApi.peopleExport("u-1", "json", "Access request by the person").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("person_reporting_off");
  });

  it("writes only the settings given, and the limits with null for none", async () => {
    fetchMock.mockImplementation(async () => answer(200, {}));

    await usageApi.updateSettings({ min_group_size: 10 });
    await usageApi.updateLimits({ rpm_limit: 600, tpm_limit: null, tokens_per_day: null });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/usage/settings");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PUT", body: '{"min_group_size":10}' });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/admin/usage/limits");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ rpm_limit: 600, tpm_limit: null, tokens_per_day: null });
  });

  it("names the downloads", () => {
    expect(usageApi.meExportUrl("csv")).toBe("/api/usage/me/export?format=csv");
    expect(usageApi.statementUrl("de")).toBe("/api/admin/usage/statement?language=de");
  });

  it("reads the file name of a download, without path parts", () => {
    const named = (value: string) => attachmentName({ headers: new Headers({ "content-disposition": value }) });
    expect(named('attachment; filename="usage-2026.json"')).toBe("usage-2026.json");
    expect(named("attachment; filename=usage.csv")).toBe("usage.csv");
    expect(named("attachment; filename*=UTF-8''Nutzung%20M%C3%BCller.csv")).toBe("Nutzung Müller.csv");
    expect(named('attachment; filename="../../etc/passwd"')).toBe("passwd");
    expect(attachmentName({ headers: new Headers() })).toBeNull();
  });

  it("carries a refusal's code", async () => {
    fetchMock.mockImplementation(async () => answer(403, { detail: { code: "not_usage_auditor" } }));

    const err = await usageApi.people({ person: "u-1", reason: "Access request by the person" }).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.code).toBe("not_usage_auditor");
  });

  it("carries the fields a settings write was refused for", async () => {
    fetchMock.mockImplementation(async () => answer(422, { detail: { problems: [{ field: "min_group_size", code: "out_of_range" }] } }));

    const err = await usageApi.updateSettings({ min_group_size: 3 }).catch((e) => e);

    expect(err).toBeInstanceOf(UsageProblemsError);
    expect(err.problems).toEqual([{ field: "min_group_size", code: "out_of_range" }]);
  });
});

describe("a reason for a per-person view", () => {
  it("takes 10 to 500 characters, spaces at the ends not counted", () => {
    expect(reasonOk("too short")).toBe(false);
    expect(reasonOk("   abcdefghi   ")).toBe(false);
    expect(reasonOk("Budget dispute")).toBe(true);
    expect(reasonOk("x".repeat(500))).toBe(true);
    expect(reasonOk("x".repeat(501))).toBe(false);
  });
});
