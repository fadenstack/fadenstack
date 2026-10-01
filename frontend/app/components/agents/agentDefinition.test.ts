import { describe, expect, it } from "vitest";

import type { AgentVersion } from "~/api/agents";

import {
  EMPTY_DEFINITION,
  changedFields,
  cleanDefinition,
  definitionOf,
  definitionProblems,
  parseAgentImport,
  serverToolsMode,
  slugFromName,
  suggestionsOf,
  withServerToolsMode,
  withUi,
} from "./agentDefinition";

const VERSION: AgentVersion = {
  ...EMPTY_DEFINITION,
  id: "v",
  version: 3,
  created_at: "",
  published_at: null,
  created_by: null,
  instructions: "Be brief.",
  host_tool_contract: [{ name: "read_page", risk: "read" }],
};

describe("agent definitions", () => {
  it("are a version without its metadata", () => {
    const definition = definitionOf(VERSION);
    expect(definition).not.toHaveProperty("version");
    expect(definition.instructions).toBe("Be brief.");
    expect(definitionOf(null)).toEqual(EMPTY_DEFINITION);
  });

  it("are cleaned before saving: trimmed, nameless tools and empty UI settings dropped", () => {
    const clean = cleanDefinition({
      ...EMPTY_DEFINITION,
      instructions: "  Hi  ",
      model_alias: " ",
      host_tool_contract: [{ name: " stamp " }, { name: "" }],
      ui: { greeting: " ", suggestions: [], theme: "x" },
    });
    expect(clean).toMatchObject({ instructions: "Hi", model_alias: null, host_tool_contract: [{ name: "stamp" }] });
    expect(clean.ui).toEqual({ theme: "x" });
  });

  it("differ only where a field really changed", () => {
    const before = definitionOf(VERSION);
    expect(changedFields(before, { ...before, instructions: "Be brief. " })).toEqual([]);
    expect(changedFields(before, { ...before, ui: { b: 1, a: 2 } })).toEqual(["ui"]);
    expect(changedFields({ ...before, ui: { a: 2, b: 1 } }, { ...before, ui: { b: 1, a: 2 } })).toEqual([]);
    expect(changedFields(before, { ...before, local_mcp_mode: "allow", knowledge: ["kb"] })).toEqual([
      "knowledge",
      "local_mcp_mode",
    ]);
  });

  it("refuse two tools of one name", () => {
    const definition = { ...EMPTY_DEFINITION, host_tool_contract: [{ name: "a" }, { name: " a" }] };
    expect(definitionProblems(definition)).toEqual(["agents.definition.problem_duplicate_tools"]);
    expect(definitionProblems(EMPTY_DEFINITION)).toEqual([]);
  });

  it("keep the SDKs' UI settings and the gateway tools in their own shapes", () => {
    const withSuggestions = withUi(EMPTY_DEFINITION, { suggestions: ["One", "Two"] });
    expect(suggestionsOf(withSuggestions)).toEqual(["One", "Two"]);
    expect(serverToolsMode(EMPTY_DEFINITION)).toBe("default");
    expect(serverToolsMode(withServerToolsMode(EMPTY_DEFINITION, "none"))).toBe("none");
    expect(withServerToolsMode({ ...EMPTY_DEFINITION, server_tools: ["knowledge_*"] }, "listed").server_tools).toEqual([
      "knowledge_*",
    ]);
  });
});

describe("importing", () => {
  it("takes an agent as the API takes it", () => {
    const imported = parseAgentImport(
      JSON.stringify({
        slug: "excel-assistant",
        name: "Excel assistant",
        version: { instructions: "x", host_tool_contract: [{ name: "write_cells", risk: "write", extra: 1 }] },
      }),
    );
    expect(imported.slug).toBe("excel-assistant");
    expect(imported.definition.host_tool_contract).toEqual([{ name: "write_cells", risk: "write" }]);
    expect(imported.definition.local_mcp_mode).toBe("disable");
  });

  it("takes a contract alone", () => {
    expect(parseAgentImport('[{"name": "read_page"}]').definition.host_tool_contract).toEqual([{ name: "read_page" }]);
  });

  it("says what is wrong", () => {
    expect(() => parseAgentImport("{")).toThrow("agents.import.not_json");
    expect(() => parseAgentImport('{"hello": 1}')).toThrow("agents.import.not_definition");
    expect(() => parseAgentImport('[{"risk": "read"}]')).toThrow("agents.import.bad_tool");
  });
});

describe("slugs", () => {
  it("come from names", () => {
    expect(slugFromName("Excel Assistant (EPLAN) ")).toBe("excel-assistant-eplan");
    expect(slugFromName("Prüfung")).toBe("prufung");
    expect(slugFromName("x".repeat(80))).toHaveLength(64);
  });
});
