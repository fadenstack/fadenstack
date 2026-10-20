import { describe, expect, it } from "vitest";
import { normalizePIIPolicy } from "./pii";

describe("PII policy profile migration", () => {
  it("retains a legacy policy's explicit zero and unsupported categories", () => {
    const result = normalizePIIPolicy({ presidio: { threshold: 0, language: "de", entities: ["PERSON", "CUSTOM_ID"] } });
    expect(result.presidio).toMatchObject({ threshold: 0, language: "de", entities: ["PERSON", "CUSTOM_ID"], threshold_mode: "legacy", profile_id: null });
  });

  it("retains the administrator's explicit installed profile selection", () => {
    const result = normalizePIIPolicy({ presidio: { profile_id: "gliner2-multilingual-pii", threshold_mode: "profile", threshold: .6, entities: ["PERSON"] } });
    expect(result.presidio.profile_id).toBe("gliner2-multilingual-pii");
    expect(result.presidio.threshold_mode).toBe("profile");
  });
});
