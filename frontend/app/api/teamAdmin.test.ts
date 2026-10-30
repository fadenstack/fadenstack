/** The team calls with their place in the tree, and the tree's refusals kept by code. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./http";
import { teamAdmin, teamErrorText } from "./teamAdmin";

const fetchMock = vi.fn();
const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("team calls", () => {
  it("sends the parent and the cost centre, and archives and brings back", async () => {
    fetchMock.mockImplementation(async () => answer(200, {}));

    await teamAdmin.update("g-1", { parent_id: "g-0", cost_center: "4711" });
    await teamAdmin.archive("g-1");
    await teamAdmin.unarchive("g-1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/groups/g-1");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PATCH", body: '{"parent_id":"g-0","cost_center":"4711"}' });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/admin/groups/g-1/archive");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/admin/groups/g-1/unarchive");
  });

  it("keeps a refusal's code, which the console words", async () => {
    fetchMock.mockImplementation(async () => answer(422, { detail: { code: "team_cycle" } }));

    const err = await teamAdmin.update("g-1", { parent_id: "g-2" }).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(teamErrorText(err, (key) => `[${key}]`)).toBe("[groups.errors.team_cycle]");
    expect(teamErrorText(new Error("boom"), (key) => key)).toBe("boom");
  });
});
