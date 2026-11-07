import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { helpUrl, pageHelpDocs, resolvePageHelp } from "./pageHelp";

/** Help's pages, which the frontend image builds and serves at /help (tests run in frontend/). */
const PAGES = join(process.cwd(), "..", "docs", "help", "pages");
/** The German ones, served at /help/de. */
const PAGES_DE = join(process.cwd(), "..", "docs", "help", "pages-de");

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

  it("knows a tab of its own: Settings > Mail links to the Mail page", () => {
    expect(resolvePageHelp("/admin/settings", "?tab=mail")?.doc).toBe("administer/mail");
    expect(resolvePageHelp("/admin/settings", "?tab=mail")?.titleKey).toBe("page_help.settings_mail.title");
    expect(resolvePageHelp("/admin/settings", "?tab=modules")?.doc).toBe("administer/index");
    expect(resolvePageHelp("/admin/settings")?.doc).toBe("administer/index");
  });

  it("links to a section of a Help page", () => {
    expect(resolvePageHelp("/admin/usage")?.doc).toBe("administer/usage#the-usage-page");
    expect(helpUrl("administer/usage#the-usage-page")).toBe("/help/administer/usage/#the-usage-page");
    expect(helpUrl("administer/usage#the-usage-page", "de")).toBe("/help/de/administer/usage/#the-usage-page");
  });

  it("links a German console to the German Help, the other languages to the English", () => {
    expect(helpUrl("operate/clusters", "de")).toBe("/help/de/operate/clusters/");
    expect(helpUrl(undefined, "de-DE")).toBe("/help/de/");
    expect(helpUrl("operate/clusters", "fr")).toBe("/help/operate/clusters/");
  });

  it("every page the console links to exists in German too", () => {
    const missing = pageHelpDocs().filter(
      (doc) => !existsSync(join(PAGES_DE, `${doc}.md`)),
    );
    expect(missing).toEqual([]);
  });
});
