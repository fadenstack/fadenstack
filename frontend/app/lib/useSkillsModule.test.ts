/**
 * Skills in the chat for everyone who may use them, not only administrators.
 *
 * The hook read the list of modules, which needs `modules:read`: a default
 * user got 403, so the chat showed no skills button, no `$` picker and no
 * `/skill`, although the user held every skills permission.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as servicesApi from "~/api/services";
import * as skillsApi from "~/api/skills";

import { useSkillsModule } from "./useSkillsModule";

afterEach(() => vi.restoreAllMocks());

describe("useSkillsModule", () => {
  it("asks the skills API, not the administrators' module list", async () => {
    const status = vi.spyOn(skillsApi, "skillsStatus").mockResolvedValue({ enabled: true });
    const modules = vi.spyOn(servicesApi.servicesApi, "list");
    const { result } = renderHook(() => useSkillsModule());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(true);
    expect(status).toHaveBeenCalledOnce();
    expect(modules).not.toHaveBeenCalled();
  });

  it("is off when the module is off", async () => {
    vi.spyOn(skillsApi, "skillsStatus").mockResolvedValue({ enabled: false });
    const { result } = renderHook(() => useSkillsModule());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(false);
  });

  it("is off when the question fails", async () => {
    vi.spyOn(skillsApi, "skillsStatus").mockRejectedValue(new Error("API 403"));
    const { result } = renderHook(() => useSkillsModule());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(false);
  });
});
