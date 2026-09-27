/** What the chat input reads as a skill being picked with ``$``. */
import { describe, expect, it } from "vitest";

import { filterSkills, mentionAt, skillRights, type SkillSummary } from "./skills";

function skill(slug: string, name: string, description = ""): SkillSummary {
  return {
    id: slug,
    tenant_id: "default",
    name,
    slug,
    description,
    owner_id: "u",
    visibility: "private",
    activation: "manual",
    status: "published",
    enabled: true,
    priority: 50,
    tags: [],
    latest_version: 1,
    published_version: 1,
    created_by: "u",
    created_at: "",
    updated_at: "",
    access: "owner",
  };
}

describe("mentionAt", () => {
  it("finds the $name being typed at the caret", () => {
    expect(mentionAt("please use $co", 14)).toEqual({ start: 11, query: "co" });
    expect(mentionAt("$", 1)).toEqual({ start: 0, query: "" });
    expect(mentionAt("Use $Code-Rev", 13)).toEqual({ start: 4, query: "code-rev" });
  });

  it("is not a price, a word or a finished mention", () => {
    expect(mentionAt("costs US$5", 10)).toBeNull();
    expect(mentionAt("a$b", 3)).toBeNull();
    expect(mentionAt("use $code review", 16)).toBeNull();
  });
});

describe("filterSkills", () => {
  const skills = [
    skill("code-review", "Code Review"),
    skill("sql-tuning", "SQL Tuning", "Make slow queries fast; review indexes"),
    skill("tone", "Tone of Voice"),
  ];

  it("puts names that start with the query first, then the ones that contain it", () => {
    expect(filterSkills(skills, "rev").map((s) => s.slug)).toEqual(["code-review", "sql-tuning"]);
    expect(filterSkills(skills, "t").map((s) => s.slug)).toEqual(["tone", "sql-tuning"]);
  });

  it("lists them all for an empty query", () => {
    expect(filterSkills(skills, "")).toHaveLength(3);
  });
});

describe("skillRights", () => {
  it("reads the skills permissions", () => {
    const granted = new Set(["skills:read", "skills:create"]);
    expect(skillRights((p) => granted.has(p))).toEqual({
      read: true,
      create: true,
      share: false,
      publish: false,
      manage: false,
    });
  });
});
