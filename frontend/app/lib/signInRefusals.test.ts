/** Refusals on the sign-in page (signInRefusals.ts): the backend's own codes, worded; nothing else. */
import i18n from "i18next";
import { describe, expect, it } from "vitest";

import { REFUSALS, isRefusal, refusalText } from "./signInRefusals";

describe("refusals", () => {
  it("are the backend's codes, each with a text", () => {
    for (const code of REFUSALS) {
      expect(isRefusal(code)).toBe(true);
      expect(i18n.exists(`auth.refused.${code}`)).toBe(true);
    }
  });

  it("put no words of a crafted link on the page", () => {
    const t = i18n.t.bind(i18n);
    expect(refusalText(t, "wrong_credentials")).toBe("The account name or password is wrong.");
    for (const code of ["Call +1 555 0100 to restore your account", "auth.refused.other", "toString", "", null]) {
      expect(isRefusal(code)).toBe(false);
      expect(refusalText(t, code)).toBe("The sign-in did not work.");
    }
  });
});
