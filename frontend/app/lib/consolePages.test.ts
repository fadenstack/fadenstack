/**
 * Who opens the console: superusers every page; someone with `usage:read` (the platform auditor) the
 * Usage page and their profile; anyone else none.
 */
import { describe, expect, it } from "vitest";

import { consolePages, mayOpen, NAV } from "./adminConstants";

describe("console pages", () => {
  it("opens every page to a superuser", () => {
    expect(consolePages(true, [])).toBeNull();
    expect(mayOpen(null, "/admin/containers")).toBe(true);
  });

  it("opens the Usage page and the profile to someone with usage:read", () => {
    const pages = consolePages(false, ["usage:read", "audit:read"]);
    expect(pages).toEqual(["/admin/usage", "/admin/profile"]);
    expect(mayOpen(pages, "/admin/usage")).toBe(true);
    expect(mayOpen(pages, "/admin/profile")).toBe(true);
    expect(mayOpen(pages, "/admin/users")).toBe(false);
    expect(mayOpen(pages, "/admin/usage-something")).toBe(false);
  });

  it("keeps the console shut to anyone else", () => {
    expect(consolePages(false, ["chat:read", "provenance:check"])).toEqual([]);
  });

  it("has a navigation entry for every page a permission opens", () => {
    const targets = NAV.flatMap((e) => (e.kind === "leaf" ? [e.to] : e.children.map((c) => c.to)));
    expect(targets).toContain("/admin/usage");
  });
});
