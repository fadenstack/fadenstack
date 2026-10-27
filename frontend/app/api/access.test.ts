/** What the UI gates on: permissions, and maintaining an agent, which is a right but no permission. */
import { describe, expect, it } from "vitest";

import { canFrom } from "~/lib/useCan";

import { accessKeys } from "./admin";
import { agentRights } from "./agents";

const perm = (resource: string, action: string) => ({ id: `${resource}:${action}`, resource, action }) as never;

describe("access keys and agent rights", () => {
  it("a maintainer without agents permissions opens the agents pages, sees their own, creates none", () => {
    const can = canFrom(new Set(accessKeys({ permissions: [perm("chat", "use")], maintains_agents: true })));
    expect(agentRights(can)).toEqual({ open: true, create: false, seeAll: false, delete: false });
  });

  it("an auditor opens the agents pages too, sees their own, creates none", () => {
    const can = canFrom(new Set(accessKeys({ permissions: [perm("chat", "use")], inspects_agents: true })));
    expect(agentRights(can)).toEqual({ open: true, create: false, seeAll: false, delete: false });
  });

  it("deleting agents is a role's agents:delete, which maintaining does not give", () => {
    const can = canFrom(new Set(accessKeys({ permissions: [perm("agents", "delete")], maintains_agents: false })));
    expect(agentRights(can)).toMatchObject({ delete: true, create: false });
  });

  it("someone with neither does not get the agents pages", () => {
    const can = canFrom(new Set(accessKeys({ permissions: [perm("chat", "use")], maintains_agents: false })));
    expect(agentRights(can).open).toBe(false);
  });

  it("agents:read sees every agent; a superuser (*) may do everything", () => {
    expect(agentRights(canFrom(new Set(accessKeys({ permissions: [perm("agents", "read")] }))))).toEqual({
      open: true,
      create: false,
      seeAll: true,
      delete: false,
    });
    expect(agentRights(canFrom(new Set(["*"])))).toEqual({ open: true, create: true, seeAll: true, delete: true });
  });
});
