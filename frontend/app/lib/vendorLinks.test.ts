/**
 * Links back to fadenstack.com carry `ref` and the release's first two numbers, nothing else, and only to
 * Fadenstack's own sites (fadenstack-dev features/about-and-ref-links.md).
 */
import { describe, expect, it } from "vitest";

import { majorMinor, tagVendorUrl, websiteUrl } from "./vendorLinks";

describe("vendor links", () => {
  it("keeps only the first two numbers of a version", () => {
    expect(majorMinor("0.4.5")).toBe("0.4");
    expect(majorMinor("v1.12.0")).toBe("1.12");
    expect(majorMinor("0.2.0-rc.3")).toBe("0.2");
    expect(majorMinor("")).toBeUndefined();
    expect(majorMinor("main")).toBeUndefined();
    expect(majorMinor(null)).toBeUndefined();
  });

  it("tags a page of the website with ref and v, keeping its path and anchor", () => {
    expect(websiteUrl("/releases/", "console-about", "0.4.5")).toBe(
      "https://fadenstack.com/releases/?ref=console-about&v=0.4",
    );
    expect(tagVendorUrl("https://fadenstack.com/releases/#0.4.6", "console-updates", "0.4.5")).toBe(
      "https://fadenstack.com/releases/?ref=console-updates&v=0.4#0.4.6",
    );
  });

  it("leaves the version out when it is unknown, as on the sign-in page", () => {
    expect(websiteUrl("/", "console-signin")).toBe("https://fadenstack.com/?ref=console-signin");
  });

  it("tags the Help and developers sites too, and nothing else", () => {
    expect(tagVendorUrl("https://developers.fadenstack.com/", "console-about", "0.4.5")).toContain("ref=console-about");
    expect(tagVendorUrl("https://help.fadenstack.com/", "help")).toBe("https://help.fadenstack.com/?ref=help");
    for (const other of [
      "https://github.com/fadenstack/fadenstack",
      "http://fadenstack.com/",
      "https://fadenstack.com.example.org/",
      "/help/",
      "not a url",
    ]) {
      expect(tagVendorUrl(other, "console-about", "0.4.5")).toBe(other);
    }
  });

  it("never carries anything but ref and v", () => {
    const url = new URL(websiteUrl("/security/", "console-about", "0.4.5"));
    expect([...url.searchParams.keys()].sort()).toEqual(["ref", "v"]);
  });
});
