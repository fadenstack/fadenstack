/**
 * The gateway's Swagger UI: same-origin behind nginx, the exposed port in the dev stack.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { GATEWAY_DOCS_PATH, grafanaOverviewUrl, resolveApiDocsUrl } from "./serviceUrls";

function answers(body: string, status = 200) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status }));
}

afterEach(() => vi.unstubAllEnvs());

describe("resolveApiDocsUrl", () => {
  it("uses the same origin when nginx serves the docs (a full install publishes no :8001)", async () => {
    const fetcher = answers('<div id="swagger-ui"></div><script src="/gateway-static/docs/swagger-ui-bundle.js">');
    expect(await resolveApiDocsUrl(fetcher)).toBe(`${window.location.origin}${GATEWAY_DOCS_PATH}`);
    expect(fetcher).toHaveBeenCalledWith(GATEWAY_DOCS_PATH, expect.objectContaining({ credentials: "same-origin" }));
  });

  it("uses the gateway's own port when that path is only the app, missing, or unreachable", async () => {
    const direct = `${window.location.protocol}//${window.location.hostname}:8001/api/docs`;
    expect(await resolveApiDocsUrl(answers('<!doctype html><div id="root"></div>'))).toBe(direct);
    expect(await resolveApiDocsUrl(answers("Not Found", 404))).toBe(direct);
    expect(await resolveApiDocsUrl(vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")))).toBe(direct);
  });

  it("keeps an explicitly configured gateway address without asking", async () => {
    vi.stubEnv("VITE_FADEN_GATEWAY_PORT", "9001");
    const fetcher = answers("swagger-ui");
    expect(await resolveApiDocsUrl(fetcher)).toMatch(/:9001\/api\/docs$/);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("grafanaOverviewUrl", () => {
  it("goes through nginx on a full install, so it follows the console to HTTPS", () => {
    vi.stubEnv("DEV", false);
    const url = new URL(grafanaOverviewUrl("dark"));
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toBe("/grafana/d/faden-overview/faden-overview");
    expect(url.searchParams.get("theme")).toBe("dark");
  });

  it("uses Grafana's own port on the dev stack, which has no nginx", () => {
    vi.stubEnv("DEV", true);
    expect(grafanaOverviewUrl("light")).toMatch(/^http:\/\/[^/]+:3001\/grafana\/d\//);
  });

  it("keeps an explicitly configured Grafana address", () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_GRAFANA_PORT", "3333");
    expect(grafanaOverviewUrl("light")).toMatch(/:3333\/grafana\/d\//);
  });
});
