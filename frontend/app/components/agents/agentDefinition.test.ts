import { describe, expect, it } from "vitest";

import type { AgentVersion } from "~/api/agents";

import {
  DEFAULT_LEVELS,
  EMPTY_DEFINITION,
  changedFields,
  cleanDefinition,
  definitionOf,
  definitionProblems,
  maxRunningSessionsOf,
  neverOnRiskyTool,
  parseAgentImport,
  piiModeSwitch,
  serverToolsMode,
  slugFromName,
  suggestionsOf,
  TOOL_LEVELS,
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

  it("keep a tool's place whole: argument and kind together, the argument a pointer", () => {
    const withTool = (tool: object) => ({ ...EMPTY_DEFINITION, host_tool_contract: [{ name: "open", ...tool }] });
    const place = { target_argument: " /path ", target_type: "document" as const };
    expect(cleanDefinition(withTool(place)).host_tool_contract).toEqual([
      { name: "open", target_argument: "/path", target_type: "document" },
    ]);
    expect(definitionProblems(withTool(place))).toEqual([]);
    const key = "agents.definition.problem_place_incomplete";
    expect(definitionProblems(withTool({ target_type: "folder" }))).toEqual([key]);
    expect(definitionProblems(withTool({ target_argument: "/path" }))).toEqual([key]);
    expect(definitionProblems(withTool({ target_argument: "path", target_type: "site" }))).toEqual([key]);
  });

  it("take Bypass as the highest level only, never as the default", () => {
    const bypass = { ...EMPTY_DEFINITION, approvals: { default_level: "auto" as const, max_level: "bypass" as const } };
    expect(definitionOf({ ...VERSION, approvals: bypass.approvals } as unknown as AgentVersion).approvals).toEqual(bypass.approvals);
    expect(definitionProblems(bypass)).toEqual([]);
    expect(TOOL_LEVELS.at(-1)).toBe("bypass");
    expect(DEFAULT_LEVELS).not.toContain("bypass");
    const above = { ...EMPTY_DEFINITION, approvals: { default_level: "bypass" as const, max_level: "auto" as const } };
    expect(cleanDefinition(above).approvals.default_level).toBe("ask");
  });

  it("hold the agent's default and highest tool mode: Ask up to Auto unless set (sdk/38 §5)", () => {
    expect(definitionOf({ ...VERSION, approvals: undefined } as unknown as AgentVersion).approvals).toEqual({
      default_level: "ask",
      max_level: "auto",
    });
    const odd = { ...VERSION, approvals: { default_level: "bypass", max_level: "auto", standing: null } };
    expect(definitionOf(odd as unknown as AgentVersion).approvals).toEqual({ default_level: "ask", max_level: "auto" });
    const above = { ...EMPTY_DEFINITION, approvals: { default_level: "auto" as const, max_level: "ask" as const } };
    expect(definitionProblems(above)).toEqual(["agents.definition.problem_default_above_max"]);
    const research = { ...EMPTY_DEFINITION, approvals: { default_level: "read_only" as const, max_level: "ask" as const } };
    expect(definitionProblems(research)).toEqual([]);
    expect(changedFields(EMPTY_DEFINITION, research)).toEqual(["approvals"]);
    expect(parseAgentImport('{"approvals": {"default_level": "read_only", "max_level": "ask"}}').definition.approvals).toEqual(
      research.approvals,
    );
  });

  it("refuse Never ask on a tool that is neither read nor write (sdk/38 D7)", () => {
    const tools = (risk?: "read" | "write" | "destructive") => ({
      ...EMPTY_DEFINITION,
      host_tool_contract: [{ name: "update_page", approval: "never" as const, ...(risk ? { risk } : {}) }],
    });
    expect(definitionProblems(tools("read"))).toEqual([]);
    expect(definitionProblems(tools("write"))).toEqual([]);
    expect(definitionProblems(tools("destructive"))).toEqual(["agents.definition.problem_never_risky"]);
    expect(definitionProblems(tools())).toEqual(["agents.definition.problem_never_risky"]);
    expect(neverOnRiskyTool({ name: "x", risk: "destructive", approval: "always" })).toBe(false);
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
