/**
 * The shared client: a page shows what the server said, whatever shape the
 * error came in, and a 204 or a text answer does not break the caller.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, json, query, readError, requester } from "./http";

const response = (status: number, body: string, type = "application/json") =>
  new Response(status === 204 ? null : body, { status, statusText: "Status", headers: { "content-type": type } });

describe("readError", () => {
  it.each([
    ['{"detail": "Agent not found"}', "Agent not found", undefined],
    ['{"detail": [{"msg": "too short"}, {"msg": "required"}]}', "too short; required", undefined],
    ['{"detail": {"code": "no_grant", "message": "Not granted"}}', "Not granted", "no_grant"],
    ['{"error": {"message": "Rate limited", "code": "rate_limit_rpm"}}', "Rate limited", "rate_limit_rpm"],
    ["Bad gateway", "Bad gateway", undefined],
  ])("reads %s", async (body, message, code) => {
    const error = await readError(response(400, body));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe(message);
    expect(error.code).toBe(code);
    expect(error.status).toBe(400);
  });

  it("keeps the detail as sent, for a caller that reads its fields", async () => {
    const error = await readError(response(422, '{"detail": {"problems": [{"field": "host", "code": "required"}]}}'));
    expect(error.detail).toEqual({ problems: [{ field: "host", code: "required" }] });
    expect((await readError(response(400, "Bad gateway"))).detail).toBeUndefined();
  });

  it("falls back to the status when there is no body", async () => {
    expect((await readError(response(502, ""))).message).toBe("502 Status");
  });
});

describe("requester", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends JSON with the session cookie and parses JSON", async () => {
    const fetch = vi.fn().mockResolvedValue(response(200, '{"ok": true}'));
    vi.stubGlobal("fetch", fetch);
    expect(await requester("/api/x")("/y", json("POST", { a: 1 }))).toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledWith("/api/x/y", {
      method: "POST",
      body: '{"a":1}',
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });
  });

  it("answers a 204 with undefined and a text answer with its text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(204, "")).mockResolvedValueOnce(response(200, "hi", "text/plain")));
    const request = requester("");
    expect(await request("/a", json("DELETE"))).toBeUndefined();
    expect(await request("/b")).toBe("hi");
  });

  it("throws the server's message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(403, '{"detail": "You do not maintain this agent."}')));
    await expect(requester("")("/a")).rejects.toThrow("You do not maintain this agent.");
  });
});

describe("query", () => {
  it("keeps only the values that are set", () => {
    expect(query({ a: 1, b: "", c: undefined, d: null, e: false })).toBe("?a=1&e=false");
    expect(query({})).toBe("");
  });
});
