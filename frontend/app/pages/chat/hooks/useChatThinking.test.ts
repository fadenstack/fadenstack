/**
 * The thinking level kept per person: a second person signed in on the same page reads their own, not the
 * levels the first one kept (the page's cache goes with the access, at sign-out, the login page or a 401).
 */
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const prefs = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }));
vi.mock("~/api/preferences", () => ({ preferences: prefs }));

import { clearCachedAccess } from "~/lib/adminConstants";
import { resetThinkingCache, useChatThinking } from "./useChatThinking";

describe("useChatThinking", () => {
  beforeEach(() => {
    resetThinkingCache();
    prefs.get.mockReset();
    prefs.update.mockReset().mockResolvedValue({ preferences: {} });
  });

  it("forgets the first person's levels when the access is cleared", async () => {
    prefs.get.mockResolvedValueOnce({ preferences: { chat_thinking: { m: "off" } } });
    const first = renderHook(() => useChatThinking());
    await waitFor(() => expect(first.result.current.kept).toEqual({ m: "off" }));
    first.unmount();

    clearCachedAccess();
    prefs.get.mockResolvedValueOnce({ preferences: {} });
    const second = renderHook(() => useChatThinking());
    await waitFor(() => expect(prefs.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(second.result.current.kept).toEqual({}));
  });

  it("does not keep a read that was on its way when the access was cleared", async () => {
    let answer: (value: unknown) => void = () => {};
    prefs.get.mockReturnValueOnce(new Promise((done) => { answer = done; }));
    renderHook(() => useChatThinking());
    clearCachedAccess();
    answer({ preferences: { chat_thinking: { m: "high" } } });
    await new Promise((done) => setTimeout(done, 0));

    prefs.get.mockResolvedValueOnce({ preferences: {} });
    const next = renderHook(() => useChatThinking());
    await waitFor(() => expect(prefs.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(next.result.current.kept).toEqual({}));
  });
});
