/** The team rows as a tree, by path: a team moved since appears under each path it had. */
import { describe, expect, it } from "vitest";

import type { AdminUsageGroup } from "~/api/usage";
import { measures } from "~/test/usageFixtures";

import { teamTree } from "./UsageGroups";

function row(path: string[], overrides: Partial<AdminUsageGroup> = {}): AdminUsageGroup {
  return {
    key: path.at(-1) ?? null,
    label: path.at(-1) ?? null,
    other: false,
    people: 6,
    suppressed: false,
    parent_key: path.length > 1 ? path.at(-2)! : null,
    path,
    ...measures(),
    ...overrides,
  };
}

describe("teamTree", () => {
  it("places a moved team under each path it had, and each merged row under its own team", () => {
    const roots = teamTree([
      row(["Sales"]),
      row(["Sales", "Pilot"]),
      row(["Ops"]),
      row(["Ops", "Pilot"]), // moved from Sales to Ops: once per path, as recorded
      row(["Ops"], { key: null, label: null, other: true, people: null, suppressed: true, parent_key: "Ops" }),
      row([], { key: null, label: null, other: false, parent_key: null }),
    ]);

    expect(roots.map((n) => [n.id, n.kind])).toEqual([
      ["t:Ops", "team"],
      ["t:Sales", "team"],
      ["unassigned", "unassigned"],
    ]);
    const ops = roots[0];
    expect(ops.children.map((n) => n.id)).toEqual(["t:Ops/Pilot", "o:Ops"]);
    expect(ops.children[1].parentLabel).toBe("Ops");
    expect(roots[1].children.map((n) => n.id)).toEqual(["t:Sales/Pilot"]);
  });

  it("puts the top-level merged row after the teams and before Unassigned", () => {
    const roots = teamTree([
      row([], { key: null, label: null, other: false, parent_key: null }),
      row([], { key: null, label: null, other: true, people: null, suppressed: true, parent_key: null }),
      row(["Sales"]),
    ]);
    expect(roots.map((n) => n.kind)).toEqual(["team", "smaller", "unassigned"]);
  });
});
