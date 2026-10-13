/**
 * The user's language follows them: picked in the console it is kept on the server, and a
 * language picked in an agent or on another device is the console's once signed in.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const i18n = vi.hoisted(() => ({
  language: "en",
  resolvedLanguage: "en",
  reloadResources: vi.fn(async () => undefined),
  changeLanguage: vi.fn(async (code: string) => {
    i18n.language = i18n.resolvedLanguage = code;
  }),
}));
vi.mock("~/i18n", () => ({ default: i18n }));

import * as i18nApi from "~/api/i18n";
import { preferences } from "~/api/preferences";

import { applyUserLanguage, consoleLanguage, pickLanguage } from "./userLanguage";

const languages = [
  { code: "en", name: "English" },
  { code: "de", name: "Deutsch" },
  { code: "zh", name: "中文" },
];

beforeEach(() => {
  i18n.language = i18n.resolvedLanguage = "en";
  i18n.changeLanguage.mockClear();
  i18n.reloadResources.mockClear();
  vi.spyOn(i18nApi, "listLanguages").mockResolvedValue(languages);
});
afterEach(() => vi.restoreAllMocks());

describe("the user's language", () => {
  it("names a language the way the console loads it", () => {
    expect(consoleLanguage("de-AT")).toBe("de");
    expect(consoleLanguage("zh-Hans")).toBe("zh");
    expect(consoleLanguage("fr")).toBe("fr");
  });

  it("keeps a language picked in the console for every client", async () => {
    const update = vi.spyOn(preferences, "update").mockResolvedValue({ preferences: { language: "de" } });
    pickLanguage("de", ["common", "chat"]);
    await vi.waitFor(() => expect(i18n.changeLanguage).toHaveBeenCalledWith("de"));
    expect(i18n.reloadResources).toHaveBeenCalledWith(["de"], ["common", "chat"]);
    expect(update).toHaveBeenCalledWith({ language: "de" });
  });

  it("still shows a picked language when the server cannot keep it", async () => {
    vi.spyOn(preferences, "update").mockRejectedValue(new Error("API 503"));
    pickLanguage("de");
    await vi.waitFor(() => expect(i18n.resolvedLanguage).toBe("de"));
  });

  it("follows a language picked in an agent once signed in", async () => {
    vi.spyOn(preferences, "get").mockResolvedValue({ preferences: { language: "zh-Hans" } });
    await expect(applyUserLanguage(["common", "chat"])).resolves.toBe("zh");
    expect(i18n.reloadResources).toHaveBeenCalledWith(["zh"], ["common", "chat"]);
    expect(i18n.resolvedLanguage).toBe("zh");
  });

  it("leaves the console as it is without a saved language, in it already, or without its texts", async () => {
    const get = vi.spyOn(preferences, "get");
    get.mockResolvedValueOnce({ preferences: {} });
    await expect(applyUserLanguage()).resolves.toBeNull();
    get.mockResolvedValueOnce({ preferences: { language: "en-GB" } });
    await expect(applyUserLanguage()).resolves.toBeNull();
    get.mockResolvedValueOnce({ preferences: { language: "it" } });
    await expect(applyUserLanguage()).resolves.toBeNull();
    get.mockRejectedValueOnce(new Error("API 500"));
    await expect(applyUserLanguage()).resolves.toBeNull();
    expect(i18n.changeLanguage).not.toHaveBeenCalled();
    expect(i18n.resolvedLanguage).toBe("en");
  });
});
