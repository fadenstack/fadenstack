/** Who keeps a team: an administrator, or the sign-in provider that names it and keeps its members. */
import { describe, expect, it } from "vitest";

import { teamKeeper } from "./admin";

describe("teamKeeper", () => {
  it("names the provider of a team it keeps, and nobody for an administrator's", () => {
    expect(teamKeeper({ source: "provider:entra" })).toBe("entra");
    expect(teamKeeper({ source: null })).toBeNull();
    expect(teamKeeper({})).toBeNull();
  });
});
