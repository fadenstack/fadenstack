/**
 * Who opens the console: superusers every page; someone with `usage:read` the Usage page, someone with
 * `audit:read` (the platform auditor) the audit log, each with their profile; anyone else none.
 */
import { describe, expect, it } from "vitest";

import { consolePages, featureShown, mayOpen, NAV } from "./adminConstants";

describe("console pages", () => {
  it("opens every page to a superuser", () => {
    expect(consolePages(true, [])).toBeNull();
    expect(mayOpen(null, "/admin/containers")).toBe(true);
  });

  it("opens the Usage page and the profile to someone with usage:read", () => {
    const pages = consolePages(false, ["usage:read"]);
    expect(pages).toEqual(["/admin/usage", "/admin/profile"]);
    expect(mayOpen(pages, "/admin/usage")).toBe(true);
    expect(mayOpen(pages, "/admin/profile")).toBe(true);
    expect(mayOpen(pages, "/admin/users")).toBe(false);
    expect(mayOpen(pages, "/admin/logs")).toBe(false);
    expect(mayOpen(pages, "/admin/usage-something")).toBe(false);
  });

  it("opens the audit log to the platform auditor (audit:read), beside the Usage page", () => {
    const pages = consolePages(false, ["usage:read", "audit:read"]);
    expect(pages).toEqual(["/admin/usage", "/admin/logs", "/admin/audit", "/admin/profile"]);
    expect(mayOpen(pages, "/admin/logs")).toBe(true);
    expect(mayOpen(pages, "/admin/audit")).toBe(true);
    expect(mayOpen(pages, "/admin/settings")).toBe(false);

    // audit:read alone opens the console too, at the audit log.
    expect(consolePages(false, ["audit:read"])).toEqual(["/admin/logs", "/admin/audit", "/admin/profile"]);
  });

  it("keeps the console shut to anyone else", () => {
    expect(consolePages(false, ["chat:read", "provenance:check"])).toEqual([]);
  });

  it("has a navigation entry for every page a permission opens, open to that permission", () => {
    const entries = NAV.flatMap((e) => (e.kind === "leaf" ? [e] : e.children));
    const usage = entries.find((e) => e.to === "/admin/usage");
    const logs = entries.find((e) => e.to === "/admin/logs");
    expect(usage?.permission).toBe("usage:read");
    expect([logs?.permission].flat()).toContain("audit:read");
    expect(logs?.superuserOnly).toBeFalsy();
  });
});

describe("pages for a feature the server offers", () => {
  const budgets = NAV.flatMap((e) => (e.kind === "group" ? e.children : [])).find((c) => c.to === "/admin/budgets");

  it("lists Budgets under Observability, beside Usage, for superusers", () => {
    const observability = NAV.find((e) => e.id === "observability");
    expect(observability?.kind === "group" && observability.children.map((c) => c.to)).toEqual(
      expect.arrayContaining(["/admin/usage", "/admin/budgets"]),
    );
    expect(budgets).toMatchObject({ feature: "budgets", superuserOnly: true });
  });

  it("shows it only while the server offers the feature", () => {
    expect(featureShown(budgets!, new Set())).toBe(false);
    expect(featureShown(budgets!, new Set(["mail_settings"]))).toBe(false);
    expect(featureShown(budgets!, new Set(["budgets"]))).toBe(true);
    // An entry that needs no feature always shows.
    expect(featureShown({}, new Set())).toBe(true);
  });
});
