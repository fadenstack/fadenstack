import { describe, expect, it } from "vitest";

import { projectModelOf } from "./projectModel";

const projects = [
  { id: "p1", model_alias: "qwen" },
  { id: "p2", model_alias: null },
];

describe("projectModelOf", () => {
  it("is the model of the chat's project", () => {
    expect(projectModelOf({ project_id: "p1" }, projects)).toBe("qwen");
  });

  it("is null when the project has none, the chat has no project, or it is not known yet", () => {
    expect(projectModelOf({ project_id: "p2" }, projects)).toBeNull();
    expect(projectModelOf({ project_id: null }, projects)).toBeNull();
    expect(projectModelOf({ project_id: "p3" }, projects)).toBeNull();
    expect(projectModelOf(undefined, projects)).toBeNull();
  });
});
