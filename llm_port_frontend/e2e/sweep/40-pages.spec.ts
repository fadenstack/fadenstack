/**
 * Every page of the console and the chat opens, and says nothing is wrong.
 *
 * Read-only: each page is opened and looked at. A page fails on a crash (an
 * uncaught error), a server error from its API calls, an error screen, or a
 * translation key shown instead of its text. Console errors and 4xx answers
 * are recorded in the report for review, not failed: some are expected (a
 * module that is off, a resource the user may not see).
 */
import { expect, test, type APIRequestContext } from "@playwright/test";

import { Watch } from "./watch";

type Ids = Record<string, string | undefined>;

async function firstId(request: APIRequestContext, url: string, pick?: (row: any) => boolean) {
  const response = await request.get(url);
  if (!response.ok()) return undefined;
  const body = await response.json();
  const rows: any[] = Array.isArray(body) ? body : (body.items ?? body.data ?? []);
  const row = pick ? rows.find(pick) : rows[0];
  return row ? String(row.id ?? row.session_id ?? "") : undefined;
}

let ids: Promise<Ids> | null = null;

/** The ids the pages with a parameter open: the first of each, from the API. */
function resolveIds(request: APIRequestContext): Promise<Ids> {
  ids ??= (async () => ({
    container: await firstId(request, "/api/admin/containers/", (c) => /postgres/.test(c.name)),
    runtime: await firstId(request, "/api/llm/runtimes/"),
    cluster: await firstId(request, "/api/inference/environments"),
    deployment: await firstId(request, "/api/inference/deployments"),
    node: await firstId(request, "/api/admin/system/nodes"),
    mcpServer: await firstId(request, "/api/admin/mcp/servers"),
    skill: await firstId(request, "/api/skills?limit=5"),
    session: await firstId(request, "/api/chat/sessions?limit=5"),
  }))();
  return ids;
}

const PAGES: { path: string; needs?: keyof Awaited<ReturnType<typeof resolveIds>> }[] = [
  { path: "/admin/dashboard" },
  { path: "/admin/containers" },
  { path: "/admin/containers/new" },
  { path: "/admin/containers/:container", needs: "container" },
  { path: "/admin/containers/:container/edit", needs: "container" },
  { path: "/admin/images" },
  { path: "/admin/networks" },
  { path: "/admin/stacks" },
  { path: "/admin/logs" },
  { path: "/admin/audit" },
  { path: "/admin/settings" },
  { path: "/admin/users" },
  { path: "/admin/roles" },
  { path: "/admin/groups" },
  { path: "/admin/auth-providers" },
  { path: "/admin/llm/providers" },
  { path: "/admin/llm/models" },
  { path: "/admin/llm/runtimes" },
  { path: "/admin/llm/runtimes/:runtime", needs: "runtime" },
  { path: "/admin/clusters" },
  { path: "/admin/clusters/:cluster", needs: "cluster" },
  { path: "/admin/marketplace" },
  { path: "/admin/deployments" },
  { path: "/admin/deployments/:deployment", needs: "deployment" },
  { path: "/admin/nodes" },
  { path: "/admin/nodes/profiles" },
  { path: "/admin/nodes/:node", needs: "node" },
  { path: "/admin/scheduler" },
  { path: "/admin/llm/agent-trace" },
  { path: "/admin/llm/endpoint" },
  { path: "/admin/pii/dashboard" },
  { path: "/admin/pii/activity" },
  { path: "/admin/pii/policies" },
  { path: "/admin/security-map" },
  { path: "/admin/observability" },
  { path: "/admin/profile" },
  { path: "/admin/chat/projects" },
  { path: "/admin/chat/sessions" },
  { path: "/admin/chat/attachments" },
  { path: "/admin/rag/knowledge-base" },
  { path: "/admin/rag/documents" },
  { path: "/admin/rag/collections" },
  { path: "/admin/mcp/servers" },
  { path: "/admin/mcp/servers/:mcpServer", needs: "mcpServer" },
  { path: "/admin/skills" },
  { path: "/admin/skills/:skill", needs: "skill" },
  { path: "/chat" },
  { path: "/chat/:session", needs: "session" },
  { path: "/skills" },
  { path: "/skills/:skill", needs: "skill" },
];

for (const { path, needs } of PAGES) {
  test(`${path} opens`, async ({ page, request, baseURL }) => {
    let url = path;
    if (needs) {
      const id = (await resolveIds(request))[needs];
      test.skip(!id, `no ${needs} on this install`);
      url = path.replace(`:${needs}`, id!);
    }
    const watch = new Watch(page, new URL(baseURL!).origin);
    await page.goto(url);
    await watch.inspect();
    await expect(page, "sent to the sign-in page").not.toHaveURL(/\/login/);
    await watch.report(path);
    expect(watch.failures(), `${url}`).toEqual([]);
  });
}
