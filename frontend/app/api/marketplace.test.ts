/**
 * A refusal with a code (`images_disabled`, 403) keeps it, so the console can say it in its own language; and
 * the one place the console asks whether this install makes pictures.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { inferenceApi } from "./inference";
import { errorCode, marketplaceApi } from "./marketplace";

const fetchMock = vi.fn();
const NOTE = "Image generation is turned off in this version.";

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function hostPictures(): Promise<Error & { status?: number }> {
  try {
    await marketplaceApi.host({
      repo_id: "Tongyi-MAI/Z-Image-Turbo", environment_id: "e", name: "z-image-turbo", copies: 1, gpus_per_copy: 1,
      engine_config: {},
    } as never);
  } catch (e) {
    return e as Error & { status?: number };
  }
  throw new Error("the host was not refused");
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a refusal with a code", () => {
  it("hosting a picture model: the code, the server's note and the status", async () => {
    fetchMock.mockResolvedValue(answer(403, { detail: { code: "images_disabled", message: NOTE } }));
    const err = await hostPictures();
    expect(errorCode(err)).toBe("images_disabled");
    expect(err.message).toBe(NOTE);
    expect(err.status).toBe(403);
  });

  it("a deployment's change: the same", async () => {
    fetchMock.mockResolvedValue(answer(403, { detail: { code: "images_disabled", message: NOTE } }));
    const err = await inferenceApi.updateDeployment("d-1", { spec: {} } as never).catch((e: unknown) => e);
    expect(errorCode(err)).toBe("images_disabled");
  });

  it("a plain message keeps its text and has no code", async () => {
    fetchMock.mockResolvedValue(answer(409, { detail: "The cluster's runtime cannot load it." }));
    const err = await hostPictures();
    expect(err.message).toBe("The cluster's runtime cannot load it.");
    expect(errorCode(err)).toBeUndefined();
  });
});

it("asks the server whether this install makes pictures", async () => {
  fetchMock.mockResolvedValue(answer(200, { enabled: false, code: "images_disabled" }));
  await expect(marketplaceApi.imageGeneration()).resolves.toEqual({ enabled: false, code: "images_disabled" });
  expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/marketplace\/image-generation$/);
});
