/** Teams as a tree: order, depth, and the parents a team may get (no cycle, at most six levels). */
import { describe, expect, it } from "vitest";

import { asTree, depthOf, descendantsOf, heightOf, MAX_TEAM_DEPTH, parentOptions, pickableTeams } from "./teamTree";

const team = (id: string, parent_id: string | null = null, archived_at: string | null = null) => ({
  id,
  name: id,
  parent_id,
  archived_at,
});

// Sales > EMEA > Berlin; Sales > Americas; Engineering
const TEAMS = [
  team("Sales"),
  team("EMEA", "Sales"),
  team("Engineering"),
  team("Berlin", "EMEA"),
  team("Americas", "Sales"),
];

describe("the tree", () => {
  it("lists each team's sub-teams right below it, siblings by name", () => {
    expect(asTree(TEAMS).map((r) => [r.team.id, r.depth, r.hasChildren])).toEqual([
      ["Engineering", 1, false],
      ["Sales", 1, true],
      ["Americas", 2, false],
      ["EMEA", 2, true],
      ["Berlin", 3, false],
    ]);
  });

  it("puts a team whose parent is not listed at the top, and never loops", () => {
    expect(asTree([team("Orphan", "Gone")]).map((r) => r.depth)).toEqual([1]);
    const looped = [team("A", "B"), team("B", "A")];
    expect(asTree(looped).map((r) => r.team.id).sort()).toEqual(["A", "B"]);
  });

  it("knows a team's level, what is below it and how deep that goes", () => {
    expect(depthOf(TEAMS, "Berlin")).toBe(3);
    expect(depthOf(TEAMS, "Sales")).toBe(1);
    expect([...descendantsOf(TEAMS, "Sales")].sort()).toEqual(["Americas", "Berlin", "EMEA"]);
    expect(heightOf(TEAMS, "Sales")).toBe(3);
    expect(heightOf(TEAMS, "Berlin")).toBe(1);
  });
});

describe("the parents a team may get", () => {
  it("offers neither the team itself nor one of its sub-teams (no cycle), nor an archived team", () => {
    const withArchived = [...TEAMS, team("Old")];
    withArchived[withArchived.length - 1].archived_at = "2026-10-01T00:00:00Z";
    const offered = parentOptions(withArchived, "Sales").map((o) => o.team.id);
    expect(offered).toEqual(["Engineering"]);
  });

  it("marks a parent that would make the tree more than six levels deep", () => {
    // A chain of six: L1 > L2 > ... > L6.
    const chain = Array.from({ length: MAX_TEAM_DEPTH }, (_, i) => team(`L${i + 1}`, i ? `L${i}` : null));
    const forNew = parentOptions(chain, null);
    expect(forNew.find((o) => o.team.id === "L5")?.allowed).toBe(true);
    expect(forNew.find((o) => o.team.id === "L6")?.allowed).toBe(false);

    // A team with one sub-team needs two levels: below L4 at most.
    const withPair = [...chain, team("P"), team("C", "P")];
    const forPair = parentOptions(withPair, "P");
    expect(forPair.find((o) => o.team.id === "L4")?.allowed).toBe(true);
    expect(forPair.find((o) => o.team.id === "L5")?.allowed).toBe(false);
  });

  it("offers only active teams to pick", () => {
    const teams = [team("A"), team("B", null, "2026-10-01T00:00:00Z")];
    expect(pickableTeams(teams).map((r) => r.team.id)).toEqual(["A"]);
  });
});
