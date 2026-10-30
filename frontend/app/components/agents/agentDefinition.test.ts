import { describe, expect, it } from "vitest";

import type { AgentVersion } from "~/api/agents";

import {
  EMPTY_DEFINITION,
  changedFields,
  cleanDefinition,
  definitionOf,
  definitionProblems,
  maxRunningSessionsOf,
  parseAgentImport,
  piiModeSwitch,
  serverToolsMode,
  slugFromName,
  suggestionsOf,
  tokenLifetimeOf,
  withMaxRunningSessions,
  withPiiModeSwitch,
  withServerToolsMode,
  withTokenLifetime,
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

describe("the PII mode setting", () => {
  it("is null (the tenant decides), true or false", () => {
    expect(EMPTY_DEFINITION.pii).toEqual({ allow_mode_override: null });
    expect(piiModeSwitch(EMPTY_DEFINITION)).toBe("tenant");
    const allowed = withPiiModeSwitch(EMPTY_DEFINITION, "allow");
    expect(allowed.pii).toEqual({ allow_mode_override: true });
    expect(piiModeSwitch(withPiiModeSwitch(allowed, "refuse"))).toBe("refuse");
    expect(withPiiModeSwitch(allowed, "tenant").pii).toEqual({ allow_mode_override: null });
  });

  it("is a field of the definition, compared and versioned like the others", () => {
    const before = definitionOf(VERSION);
    expect(changedFields(before, withPiiModeSwitch(before, "allow"))).toEqual(["pii"]);
    expect(changedFields(before, withPiiModeSwitch(before, "tenant"))).toEqual([]);
  });

  it("reads anything but true or false as following the tenant", () => {
    const odd = { ...VERSION, pii: { allow_mode_override: "yes" } } as unknown as AgentVersion;
    expect(definitionOf(odd).pii).toEqual({ allow_mode_override: null });
    const missing = { ...VERSION, pii: undefined } as unknown as AgentVersion;
    expect(definitionOf(missing).pii).toEqual({ allow_mode_override: null });
    const imported = parseAgentImport(JSON.stringify({ version: { pii: { allow_mode_override: false, x: 1 } } }));
    expect(imported.definition.pii).toEqual({ allow_mode_override: false });
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

describe("an agent's own token lifetimes", () => {
  it("are kept in extensions.token, beside the other settings, and go when none is left", () => {
    const other = { ...EMPTY_DEFINITION, extensions: { pii: { local: true } } };
    const short = withTokenLifetime(withTokenLifetime(other, "access_s", 120), "idle_s", 3600);
    expect(short.extensions).toEqual({ pii: { local: true }, token: { access_s: 120, idle_s: 3600 } });
    expect(tokenLifetimeOf(short, "access_s")).toBe(120);
    expect(tokenLifetimeOf(short, "refresh_s")).toBeNull();

    const back = withTokenLifetime(withTokenLifetime(short, "access_s", null), "idle_s", null);
    expect(back.extensions).toEqual({ pii: { local: true } });
    expect(changedFields(other, short)).toEqual(["extensions"]);
  });

  it("are at least a minute", () => {
    expect(definitionProblems(withTokenLifetime(EMPTY_DEFINITION, "access_s", 30))).toEqual([
      "agents.definition.problem_token_short",
    ]);
    expect(definitionProblems(withTokenLifetime(EMPTY_DEFINITION, "access_s", 60))).toEqual([]);
  });
});

describe("chat sessions running at once", () => {
  it("reads 1 to 5 and nothing else", () => {
    for (const n of [1, 2, 3, 4, 5]) {
      expect(maxRunningSessionsOf({ ...EMPTY_DEFINITION, ui: { max_running_sessions: n } })).toBe(n);
    }
    for (const bad of [0, 6, 2.5, "3", true, null]) {
      expect(maxRunningSessionsOf({ ...EMPTY_DEFINITION, ui: { max_running_sessions: bad } })).toBeNull();
    }
    expect(maxRunningSessionsOf(EMPTY_DEFINITION)).toBeNull();
  });

  it("sets the key, keeps the other UI settings, and removes it for the default", () => {
    const base = { ...EMPTY_DEFINITION, ui: { greeting: "Hi" } };
    const set = withMaxRunningSessions(base, 3);
    expect(set.ui).toEqual({ greeting: "Hi", max_running_sessions: 3 });
    expect(withMaxRunningSessions(set, 2).ui).toEqual({ greeting: "Hi", max_running_sessions: 2 });
    expect(withMaxRunningSessions(set, null).ui).toEqual({ greeting: "Hi" });
    expect(withMaxRunningSessions(set, 9).ui).toEqual({ greeting: "Hi" });
    expect(base.ui).toEqual({ greeting: "Hi" });
  });

  it("counts as a change of the ui field", () => {
    expect(changedFields(EMPTY_DEFINITION, withMaxRunningSessions(EMPTY_DEFINITION, 2))).toEqual(["ui"]);
  });
});
