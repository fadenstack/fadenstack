import { describe, expect, it } from "vitest";

import { diffLines, hasChanges, parseUnifiedDiff } from "./textDiff";

describe("diffLines", () => {
  it("keeps what stayed and marks what was removed and added", () => {
    expect(diffLines("a\nb\nc", "a\nx\nc\nd")).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "x" },
      { kind: "same", text: "c" },
      { kind: "added", text: "d" },
    ]);
  });

  it("finds no change in equal texts", () => {
    expect(hasChanges(diffLines("a\nb", "a\nb"))).toBe(false);
    expect(diffLines("", "")).toEqual([]);
    expect(diffLines("", "new")).toEqual([{ kind: "added", text: "new" }]);
  });
});

describe("parseUnifiedDiff", () => {
  it("reads a server's diff without its file headers", () => {
    expect(parseUnifiedDiff("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n same")).toEqual([
      { kind: "hunk", text: "@@ -1 +1 @@" },
      { kind: "removed", text: "old" },
      { kind: "added", text: "new" },
      { kind: "same", text: "same" },
    ]);
    expect(parseUnifiedDiff("  ")).toEqual([]);
  });
});
