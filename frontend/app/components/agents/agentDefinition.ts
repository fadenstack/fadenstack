/**
 * An agent's definition as the console edits it: what a version holds,
 * without the version's own metadata. Pure functions, so the editor, the
 * create dialog and the version comparison agree on what a definition is.
 */
import type { Agent, AgentDefinition, AgentPii, AgentVersion, HostTool } from "~/api/agents";

export const EMPTY_DEFINITION: AgentDefinition = {
  instructions: "",
  model_alias: null,
  knowledge: [],
  server_tools: null,
  host_tool_contract: [],
  local_mcp_mode: "disable",
  ui: {},
  pii: { allow_mode_override: null },
  extensions: {},
};

/** The fields of a definition, in the order the console shows them. */
export const DEFINITION_FIELDS = [
  "instructions",
  "model_alias",
  "knowledge",
  "server_tools",
  "host_tool_contract",
  "local_mcp_mode",
  "ui",
  "pii",
  "extensions",
] as const satisfies readonly (keyof AgentDefinition)[];

export type DefinitionField = (typeof DEFINITION_FIELDS)[number];

/** A version's definition. */
export function definitionOf(version: AgentVersion | null | undefined): AgentDefinition {
  if (!version) return EMPTY_DEFINITION;
  return {
    instructions: version.instructions ?? "",
    model_alias: version.model_alias ?? null,
    knowledge: version.knowledge ?? [],
    server_tools: version.server_tools ?? null,
    host_tool_contract: version.host_tool_contract ?? [],
    local_mcp_mode: version.local_mcp_mode ?? "disable",
    ui: version.ui ?? {},
    pii: piiOf(version.pii),
    extensions: version.extensions ?? {},
  };
}

/** The PII settings as the console keeps them: anything but true or false follows the tenant. */
function piiOf(value: unknown): AgentPii {
  const allow = value && typeof value === "object" ? (value as Partial<AgentPii>).allow_mode_override : null;
  return { allow_mode_override: typeof allow === "boolean" ? allow : null };
}

/** A definition as it is saved: trimmed, empty values dropped, tools without a name left out. */
export function cleanDefinition(definition: AgentDefinition): AgentDefinition {
  return {
    ...definition,
    instructions: definition.instructions.trim(),
    model_alias: definition.model_alias?.trim() || null,
    host_tool_contract: definition.host_tool_contract
      .map(cleanTool)
      .filter((tool) => tool.name !== ""),
    ui: cleanUi(definition.ui),
    pii: piiOf(definition.pii),
  };
}

function cleanTool(tool: HostTool): HostTool {
  const clean: HostTool = { name: tool.name.trim() };
  if (tool.risk) clean.risk = tool.risk;
  if (tool.approval) clean.approval = tool.approval;
  if (tool.schema_sha256) clean.schema_sha256 = tool.schema_sha256;
  return clean;
}

function cleanUi(ui: Record<string, unknown>): Record<string, unknown> {
  const out = { ...ui };
  if (typeof out.greeting === "string" && out.greeting.trim() === "") delete out.greeting;
  if (Array.isArray(out.suggestions) && out.suggestions.length === 0) delete out.suggestions;
  return out;
}

/** The fields in which two definitions differ (after cleaning). */
export function changedFields(before: AgentDefinition, after: AgentDefinition): DefinitionField[] {
  const a = cleanDefinition(before);
  const b = cleanDefinition(after);
  return DEFINITION_FIELDS.filter((f) => stable(a[f]) !== stable(b[f]));
}

export function sameDefinition(a: AgentDefinition, b: AgentDefinition): boolean {
  return changedFields(a, b).length === 0;
}

/** JSON with sorted keys, so key order never counts as a change. */
function stable(value: unknown): string {
  return JSON.stringify(value, (_, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([x], [y]) => x.localeCompare(y)))
      : v,
  );
}

/** What is wrong with a definition, as translation keys (empty: it can be saved). */
export function definitionProblems(definition: AgentDefinition): string[] {
  const names = definition.host_tool_contract.map((t) => t.name.trim()).filter(Boolean);
  const problems: string[] = [];
  if (new Set(names).size !== names.length) problems.push("agents.definition.problem_duplicate_tools");
  if (TOKEN_LIFETIMES.some((f) => (tokenLifetimeOf(definition, f) ?? MIN_TOKEN_LIFETIME_S) < MIN_TOKEN_LIFETIME_S)) {
    problems.push("agents.definition.problem_token_short");
  }
  return problems;
}

// ── The SDKs' UI settings ─────────────────────────────────────────────────

export function greetingOf(definition: AgentDefinition): string {
  return typeof definition.ui.greeting === "string" ? definition.ui.greeting : "";
}

export function suggestionsOf(definition: AgentDefinition): string[] {
  const value = definition.ui.suggestions;
  return Array.isArray(value) ? value.filter((s): s is string => typeof s === "string") : [];
}

export function withUi(
  definition: AgentDefinition,
  changes: { greeting?: string; suggestions?: string[] },
): AgentDefinition {
  return { ...definition, ui: { ...definition.ui, ...changes } };
}

// ── Chat sessions running at once ────────────────────────────────────────

/** The most chat sessions an agent may run at once on a device; also what a client uses when none is set. */
export const MAX_RUNNING_SESSIONS = 5;

/** `ui.max_running_sessions`: a whole number from 1 to 5, or null (not set: the client's default). */
export function maxRunningSessionsOf(definition: AgentDefinition): number | null {
  const value = definition.ui.max_running_sessions;
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= MAX_RUNNING_SESSIONS ? value : null;
}

/** Sets the limit; null (or anything outside 1 to 5) removes the key. */
export function withMaxRunningSessions(definition: AgentDefinition, count: number | null): AgentDefinition {
  const rest = { ...definition.ui };
  delete rest.max_running_sessions;
  const valid = count !== null && Number.isInteger(count) && count >= 1 && count <= MAX_RUNNING_SESSIONS;
  return { ...definition, ui: valid ? { ...rest, max_running_sessions: count } : rest };
}

// ── PII ───────────────────────────────────────────────────────────────────

/** `pii.allow_mode_override`: null follows the tenant's setting, true allows, false refuses. */
export type PiiModeSwitch = "tenant" | "allow" | "refuse";

export function piiModeSwitch(definition: AgentDefinition): PiiModeSwitch {
  const allow = definition.pii?.allow_mode_override ?? null;
  return allow === null ? "tenant" : allow ? "allow" : "refuse";
}

export function withPiiModeSwitch(definition: AgentDefinition, mode: PiiModeSwitch): AgentDefinition {
  const allow = mode === "tenant" ? null : mode === "allow";
  return { ...definition, pii: { ...definition.pii, allow_mode_override: allow } };
}

// ── Gateway tools ─────────────────────────────────────────────────────────

/** `server_tools`: null offers what a chat gets, [] none, a list only those. */
export type ServerToolsMode = "default" | "none" | "listed";

export function serverToolsMode(definition: AgentDefinition): ServerToolsMode {
  if (definition.server_tools === null) return "default";
  return definition.server_tools.length === 0 ? "none" : "listed";
}

export function withServerToolsMode(definition: AgentDefinition, mode: ServerToolsMode): AgentDefinition {
  const listed = definition.server_tools ?? [];
  return {
    ...definition,
    server_tools: mode === "default" ? null : mode === "none" ? [] : listed.length ? listed : [""],
  };
}

// ── Token lifetimes (`extensions.token`, offered by the server's agent_token_lifetimes) ─────────

/** An agent's own token lifetimes, in seconds, each optional: shorter than the tenant's settings. */
export const TOKEN_LIFETIMES = ["access_s", "refresh_s", "idle_s"] as const;
export type TokenLifetime = (typeof TOKEN_LIFETIMES)[number];
/** The key of the setting in `extensions`. */
export const TOKEN_SETTING = "token";
/** The shortest lifetime the server takes, in seconds. */
export const MIN_TOKEN_LIFETIME_S = 60;

function tokenSetting(definition: AgentDefinition): Record<string, unknown> {
  const token = definition.extensions[TOKEN_SETTING];
  return token && typeof token === "object" && !Array.isArray(token) ? (token as Record<string, unknown>) : {};
}

/** The agent's own lifetime in seconds, or null: the tenant's. */
export function tokenLifetimeOf(definition: AgentDefinition, field: TokenLifetime): number | null {
  const seconds = tokenSetting(definition)[field];
  return typeof seconds === "number" ? seconds : null;
}

/** One lifetime set, or with null dropped; the setting goes once none is left. */
export function withTokenLifetime(definition: AgentDefinition, field: TokenLifetime, seconds: number | null): AgentDefinition {
  const token = { ...tokenSetting(definition) };
  if (seconds === null) delete token[field];
  else token[field] = seconds;
  const extensions = { ...definition.extensions };
  if (Object.keys(token).length > 0) extensions[TOKEN_SETTING] = token;
  else delete extensions[TOKEN_SETTING];
  return { ...definition, extensions };
}

// ── Agents ────────────────────────────────────────────────────────────────

/** A slug from a name: lower case, words joined by dashes, at most 64 characters. */
export function slugFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
}

export const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

/** The latest version is not the one users get (or none is published yet). */
export function hasUnpublishedVersion(agent: Agent): boolean {
  return agent.latest_version !== null && agent.latest_version !== agent.published_version;
}

// ── Importing a definition ─────────────────────────────────────────────────

export interface ImportedAgent {
  slug?: string;
  name?: string;
  description?: string;
  definition: AgentDefinition;
}

/**
 * A definition from JSON: an agent as `POST /api/agents` takes it (`{slug, name, version: {...}}`),
 * a version's fields alone, or a host tool contract (a list of tools). Throws a translation key.
 */
export function parseAgentImport(text: string): ImportedAgent {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("agents.import.not_json");
  }
  if (Array.isArray(value)) {
    return { definition: { ...EMPTY_DEFINITION, host_tool_contract: value.map(toolFrom) } };
  }
  if (!value || typeof value !== "object") throw new Error("agents.import.not_definition");
  const record = value as Record<string, unknown>;
  const fields = (record.version && typeof record.version === "object" ? record.version : record) as Record<string, unknown>;
  const known = DEFINITION_FIELDS.filter((f) => f in fields);
  if (known.length === 0) throw new Error("agents.import.not_definition");
  const definition = { ...EMPTY_DEFINITION, ...Object.fromEntries(known.map((f) => [f, fields[f]])) } as AgentDefinition;
  definition.host_tool_contract = (definition.host_tool_contract ?? []).map(toolFrom);
  definition.pii = piiOf(definition.pii);
  return {
    slug: typeof record.slug === "string" ? record.slug : undefined,
    name: typeof record.name === "string" ? record.name : undefined,
    description: typeof record.description === "string" ? record.description : undefined,
    definition,
  };
}

function toolFrom(value: unknown): HostTool {
  if (!value || typeof value !== "object" || typeof (value as HostTool).name !== "string") {
    throw new Error("agents.import.bad_tool");
  }
  return cleanTool(value as HostTool);
}
