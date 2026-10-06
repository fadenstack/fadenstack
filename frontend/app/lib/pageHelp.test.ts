import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { helpUrl, pageHelpDocs, resolvePageHelp } from "./pageHelp";

/** Help's pages, which the frontend image builds and serves at /help (tests run in frontend/). */
const PAGES = join(process.cwd(), "..", "docs", "help", "pages");

describe("links from the console to Help", () => {
  it("every page the console links to exists in docs/help", () => {
    const missing = pageHelpDocs().filter(
      (doc) => !existsSync(join(PAGES, `${doc}.md`)),
    );
    expect(missing).toEqual([]);
  });

  it("links a page to its Help page under /help", () => {
    expect(resolvePageHelp("/admin/clusters/89ed5f75")?.doc).toBe(
      "operate/clusters",
    );
    expect(helpUrl("operate/clusters")).toBe("/help/operate/clusters/");
    expect(helpUrl()).toBe("/help/");
  });
});
