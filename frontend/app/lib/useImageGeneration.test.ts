/**
 * The console asks the server whether pictures may be made, and fails closed.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { marketplaceApi } from "~/api/marketplace";

import { useImageGeneration } from "./useImageGeneration";

afterEach(() => vi.restoreAllMocks());

describe("useImageGeneration", () => {
  it("is off until the server answers, and stays off when it says so", async () => {
    const ask = vi.spyOn(marketplaceApi, "imageGeneration").mockResolvedValue({ enabled: false, code: "images_disabled" });
    const { result } = renderHook(() => useImageGeneration());
    expect(result.current).toBe(false);
    await waitFor(() => expect(ask).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });

  it("is on only when the server says enabled", async () => {
    vi.spyOn(marketplaceApi, "imageGeneration").mockResolvedValue({ enabled: true });
    const { result } = renderHook(() => useImageGeneration());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("treats a failed request as off", async () => {
    const ask = vi.spyOn(marketplaceApi, "imageGeneration").mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useImageGeneration());
    await waitFor(() => expect(ask).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });
});
