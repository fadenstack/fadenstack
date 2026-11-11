/**
 * The code of an app's sign-in (pairing): how the field shows it, how the page takes it from the link's
 * fragment, and how it is kept -- in this tab, for 10 minutes at most, never in an address -- for the way
 * through the sign-in page.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  codeLetters,
  dropKeptCode,
  formatCode,
  isCompleteCode,
  keepCode,
  keptCode,
  PAIR_PATH,
  signInTargetWhenNoneGiven,
  takeCodeFromAddress,
} from "./pairCode";

afterEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.useRealTimers();
});

describe("the code field", () => {
  it("reads what was typed as the code's letters: upper case, no dash, space, vowel or digit", () => {
    expect(codeLetters("bdwp-htqx")).toBe("BDWPHTQX");
    expect(codeLetters(" b d w p  h t q x ")).toBe("BDWPHTQX");
    // 0/O and 1/I never occur in a code, nor any vowel or digit: they are not taken.
    expect(codeLetters("A0O1IbE")).toBe("B");
  });

  it("puts the dash in once a fifth letter follows, and stops at eight", () => {
    expect(formatCode("b")).toBe("B");
    expect(formatCode("bdwp")).toBe("BDWP");
    expect(formatCode("bdwph")).toBe("BDWP-H");
    expect(formatCode("BDWP-HTQX")).toBe("BDWP-HTQX");
    expect(formatCode("bdwphtqxzz")).toBe("BDWP-HTQX");
  });

  it("is complete with all eight letters only", () => {
    expect(isCompleteCode("BDWP-HTQ")).toBe(false);
    expect(isCompleteCode("bdwp htqx")).toBe(true);
  });
});

describe("the code in the link", () => {
  it("is taken from the fragment and dropped from the address bar, the rest of the address kept", () => {
    window.history.replaceState({ idx: 3 }, "", "/pair?lang=de#code=bdwp-htqx");
    expect(takeCodeFromAddress()).toBe("BDWP-HTQX");
    expect(window.location.hash).toBe("");
    expect(`${window.location.pathname}${window.location.search}`).toBe("/pair?lang=de");
    // The router's state survives the change.
    expect(window.history.state).toEqual({ idx: 3 });
  });

  it("leaves an address without a code as it is", () => {
    window.history.replaceState(null, "", "/pair#section");
    expect(takeCodeFromAddress()).toBeNull();
    expect(window.location.hash).toBe("#section");
    window.history.replaceState(null, "", "/pair");
    expect(takeCodeFromAddress()).toBeNull();
  });
});

describe("keeping the code for the way through the sign-in", () => {
  it("keeps a complete code in this tab's sessionStorage, never in localStorage", () => {
    keepCode("bdwp-htqx");
    expect(keptCode()).toBe("BDWP-HTQX");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("BDWP");
    dropKeptCode();
    expect(keptCode()).toBeNull();
  });

  it("keeps no incomplete code", () => {
    keepCode("BDWP-H");
    expect(keptCode()).toBeNull();
  });

  it("forgets a code after 10 minutes, as long as one works", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-30T12:00:00Z"));
    keepCode("BDWP-HTQX");
    vi.setSystemTime(new Date("2026-10-30T12:09:59Z"));
    expect(keptCode()).toBe("BDWP-HTQX");
    vi.setSystemTime(new Date("2026-10-30T12:10:00Z"));
    expect(keptCode()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("ignores what it did not write", () => {
    window.sessionStorage.setItem("faden-pair-code", "BDWP-HTQX");
    expect(keptCode()).toBeNull();
    window.sessionStorage.setItem("faden-pair-code", JSON.stringify({ code: "<b>", at: Date.now() }));
    expect(keptCode()).toBeNull();
  });

  it("sends the sign-in page to /pair when its address names no target and a code waits", () => {
    expect(signInTargetWhenNoneGiven("/admin/dashboard")).toBe("/admin/dashboard");
    keepCode("BDWP-HTQX");
    expect(signInTargetWhenNoneGiven("/admin/dashboard")).toBe(PAIR_PATH);
  });
});
