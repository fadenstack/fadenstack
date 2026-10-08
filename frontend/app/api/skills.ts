/**
 * Skills API — one API for everyone, served by the backend at /api/skills.
 *
 * What a caller may do is RBAC (skills:read/create/share/publish/manage) plus
 * the skill's owner, who it is shared with and who sees it; the admin pages
 * use the same routes with the admin's rights.
 */

import { query, requester } from "./http";
import { principalIds, type PrincipalOption, type PrincipalType } from "./principals";

export type { PrincipalOption, PrincipalType } from "./principals";

const BASE = "/api/skills";
const request = requester(BASE);

// ── Types ──────────────────────────────────────────────────────────────────

/** Who sees a skill: its owner (and who it is shared with), the tenant, everyone. */
export type SkillVisibility = "private" | "tenant" | "global";
/** When it comes into a chat without being picked: always, on its triggers, never. */
export type SkillActivation = "always" | "auto" | "manual";
export type SkillStatus = "draft" | "published" | "archived";
/** What the caller may do with a skill. */
export type SkillAccess = "read" | "write" | "owner" | "admin";
export type SkillListView = "all" | "mine" | "shared" | "org";
export type GrantAccess = "read" | "write";

export interface SkillTriggerRules {
  keywords?: string[];
  file_types?: string[];
  intents?: string[];
}

/** Fadenstack's part of a version (faden.yaml): tools, knowledge, triggers. */
export interface SkillManifest {
  allowed_tools?: string[];
  preferred_tools?: string[];
  forbidden_tools?: string[];
  knowledge_sources?: string[];
  trigger_rules?: SkillTriggerRules;
}

export interface SkillSummary {
  id: string;
  tenant_id: string;
  name: string;
  slug: string;
  description: string;
  /** The user who owns it; null when the organization does. */
  owner_id: string | null;
  visibility: SkillVisibility;
  activation: SkillActivation;
  status: SkillStatus;
  enabled: boolean;
  priority: number;
  tags: string[];
  latest_version: number;
  /** The version chats get; null before the first publish. */
  published_version: number | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  access: SkillAccess;
}

export interface SkillFile {
  path: string;
  size: number;
  kind: "text" | "binary";
  /** A script: kept for export, never run. */
  executable: boolean;
}

export interface SkillGrant {
  principal_type: PrincipalType;
  principal_id: string;
  access: GrantAccess;
  created_at?: string;
}

export interface SkillDetail extends SkillSummary {
  /** The version shown: the latest for editors, the published one for readers. */
  version: number;
  body_markdown: string;
  frontmatter: Record<string, unknown>;
  manifest: SkillManifest;
  files: SkillFile[];
  content_sha256: string;
  /** Who it is shared with; for its owner and administrators. */
  grants: SkillGrant[] | null;
  uses_30d: number;
}

export interface SkillList {
  items: SkillSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface SkillVersionSummary {
  version: number;
  description: string;
  content_sha256: string;
  change_note: string | null;
  created_by: string | null;
  created_at: string;
}

export interface SkillVersionDetail extends SkillVersionSummary {
  body_markdown: string;
  frontmatter: Record<string, unknown>;
  manifest: SkillManifest;
  files: SkillFile[];
}

export interface CreateSkillPayload {
  name: string;
  slug?: string;
  description?: string;
  body_markdown?: string;
  /** A whole SKILL.md in place of the fields. */
  content?: string;
  manifest?: SkillManifest;
  /** "organization": owned by the organization (administrators only). */
  owner?: "me" | "organization";
  visibility?: SkillVisibility;
  activation?: SkillActivation;
  priority?: number;
  tags?: string[];
  publish?: boolean;
  change_note?: string;
}

export interface UpdateSkillPayload {
  name?: string;
  tags?: string[];
  activation?: SkillActivation;
  priority?: number;
  enabled?: boolean;
}

export interface SaveVersionPayload {
  description?: string;
  body_markdown?: string;
  manifest?: SkillManifest;
  /** The version the editor started from: a save on top of a newer one is refused. */
  base_version?: number;
  publish?: boolean;
  change_note?: string;
}

export interface SaveVersionResult {
  version: SkillVersionSummary;
  /** False when nothing had changed. */
  created: boolean;
  skill: SkillDetail;
}

/** A skill the chat's model proposed; nothing is saved until the user saves it. */
export interface SkillDraft {
  slug: string;
  name: string;
  description: string;
  body_markdown: string;
  content: string;
}

export interface DraftSkillRequest {
  model: string;
  session_id?: string;
  guidance?: string;
  skill_id?: string;
}

export type AssignmentTargetType =
  | "tenant"
  | "global"
  | "workspace"
  | "project"
  | "assistant";

export interface SkillAssignment {
  id: string;
  skill_id: string;
  target_type: AssignmentTargetType;
  target_id: string | null;
  enabled: boolean;
  priority_override: number | null;
  created_at: string;
}

export interface CreateAssignmentPayload {
  target_type: AssignmentTargetType;
  target_id?: string | null;
  priority_override?: number | null;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function id(value: string): string {
  return encodeURIComponent(value);
}

// ── Skills ─────────────────────────────────────────────────────────────────

/** Whether the Skills module is on; anyone who may use skills may ask. */
export async function skillsStatus(): Promise<{ enabled: boolean }> {
  return request<{ enabled: boolean }>("/status");
}

export async function listSkills(params?: {
  view?: SkillListView;
  q?: string;
  status?: SkillStatus;
  visibility?: SkillVisibility;
  /** Only the ones a chat can be given: published and enabled. */
  usable?: boolean;
  limit?: number;
  offset?: number;
}): Promise<SkillList> {
  const qs = new URLSearchParams();
  if (params?.view) qs.set("view", params.view);
  if (params?.q) qs.set("q", params.q);
  if (params?.status) qs.set("status", params.status);
  if (params?.visibility) qs.set("visibility", params.visibility);
  if (params?.usable) qs.set("usable", "true");
  qs.set("limit", String(params?.limit ?? 200));
  if (params?.offset) qs.set("offset", String(params.offset));
  return request<SkillList>(`?${qs}`);
}

export async function getSkill(skillId: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}`);
}

export async function createSkill(payload: CreateSkillPayload): Promise<SkillDetail> {
  return request<SkillDetail>("", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateSkill(
  skillId: string,
  payload: UpdateSkillPayload,
): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteSkill(skillId: string): Promise<void> {
  return request<void>(`/${id(skillId)}`, { method: "DELETE" });
}

// ── Versions ───────────────────────────────────────────────────────────────

export async function saveVersion(
  skillId: string,
  payload: SaveVersionPayload,
): Promise<SaveVersionResult> {
  return request<SaveVersionResult>(`/${id(skillId)}/versions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listVersions(skillId: string): Promise<SkillVersionSummary[]> {
  return request<SkillVersionSummary[]>(`/${id(skillId)}/versions`);
}

export async function getVersion(
  skillId: string,
  version: number,
): Promise<SkillVersionDetail> {
  return request<SkillVersionDetail>(`/${id(skillId)}/versions/${version}`);
}

/** A unified diff between two versions. */
export async function diffVersions(
  skillId: string,
  from: number,
  to: number,
): Promise<string> {
  return request<string>(`/${id(skillId)}/diff?from=${from}&to=${to}`);
}

/** Serve a version to chats: the latest, or an older one to roll back. */
export async function publishSkill(skillId: string, version?: number): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}/publish`, {
    method: "POST",
    body: JSON.stringify(version ? { version } : {}),
  });
}

export async function archiveSkill(skillId: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}/archive`, { method: "POST" });
}

export async function restoreSkill(skillId: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}/restore`, { method: "POST" });
}

// ── Sharing ────────────────────────────────────────────────────────────────

export async function setVisibility(
  skillId: string,
  visibility: SkillVisibility,
): Promise<SkillDetail> {
  return request<SkillDetail>(`/${id(skillId)}/visibility`, {
    method: "PUT",
    body: JSON.stringify({ visibility }),
  });
}

export async function setGrants(
  skillId: string,
  grants: Omit<SkillGrant, "created_at">[],
): Promise<SkillGrant[]> {
  return request<SkillGrant[]>(`/${id(skillId)}/grants`, {
    method: "PUT",
    body: JSON.stringify({ grants }),
  });
}

/** People and groups to share with, by e-mail or group name. */
export async function findPrincipals(q: string): Promise<PrincipalOption[]> {
  return request<PrincipalOption[]>(`/principals${query({ q })}`);
}

/** The names of the people and groups a skill is shared with. */
export async function namePrincipals(
  grants: Pick<SkillGrant, "principal_type" | "principal_id">[],
): Promise<PrincipalOption[]> {
  if (grants.length === 0) return [];
  const ids = principalIds(grants.map((g) => ({ type: g.principal_type, id: g.principal_id })));
  return request<PrincipalOption[]>(`/principals${query({ ids })}`);
}

// ── Files ──────────────────────────────────────────────────────────────────

/** Where to download a skill: a zipped folder, or its SKILL.md alone. */
export function exportUrl(skillId: string, format: "zip" | "md", version?: number): string {
  const qs = new URLSearchParams({ format });
  if (version) qs.set("version", String(version));
  return `${BASE}/${id(skillId)}/export?${qs}`;
}

/** Create a skill from a SKILL.md or a zipped skill folder. */
export async function importSkill(
  file: File,
  options?: { owner?: "me" | "organization"; visibility?: SkillVisibility; publish?: boolean },
): Promise<SkillDetail> {
  const form = new FormData();
  form.append("file", file);
  if (options?.owner) form.append("owner", options.owner);
  if (options?.visibility) form.append("visibility", options.visibility);
  if (options?.publish !== undefined) form.append("publish", String(options.publish));
  return request<SkillDetail>("/import", { method: "POST", body: form });
}

// ── From a chat ────────────────────────────────────────────────────────────

/** Ask the chat's model to write a skill from a conversation. Saves nothing. */
export async function draftSkill(payload: DraftSkillRequest): Promise<SkillDraft> {
  return request<SkillDraft>("/draft", { method: "POST", body: JSON.stringify(payload) });
}

// ── Assignments (administrators) ───────────────────────────────────────────

export async function listAssignments(skillId: string): Promise<SkillAssignment[]> {
  return request<SkillAssignment[]>(`/${id(skillId)}/assignments`);
}

export async function createAssignment(
  skillId: string,
  payload: CreateAssignmentPayload,
): Promise<SkillAssignment> {
  return request<SkillAssignment>(`/${id(skillId)}/assignments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteAssignment(skillId: string, assignmentId: string): Promise<void> {
  return request<void>(`/${id(skillId)}/assignments/${id(assignmentId)}`, {
    method: "DELETE",
  });
}

// ── Rights ─────────────────────────────────────────────────────────────────

/** What a set of RBAC permissions ("*" for a superuser) allows with skills. */
export interface SkillRights {
  read: boolean;
  create: boolean;
  share: boolean;
  publish: boolean;
  manage: boolean;
}

export function skillRights(can: (permission: string) => boolean): SkillRights {
  return {
    read: can("skills:read"),
    create: can("skills:create"),
    share: can("skills:share"),
    publish: can("skills:publish"),
    manage: can("skills:manage"),
  };
}

// ── $mentions ──────────────────────────────────────────────────────────────

/**
 * The ``$query`` being typed at the caret, if any: ``{start, query}`` where
 * ``start`` is the index of the ``$``. A ``$`` after a letter or digit
 * (``US$5``) is not one.
 */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const match = /(^|[^\w$])\$([a-z0-9-]*)$/i.exec(before);
  if (!match) return null;
  const start = before.length - match[2].length - 1;
  return { start, query: match[2].toLowerCase() };
}

/** The skills whose slug or name matches what is being typed. */
export function filterSkills(skills: SkillSummary[], query: string): SkillSummary[] {
  const q = query.trim().toLowerCase();
  if (!q) return skills;
  const starts = skills.filter((s) => s.slug.startsWith(q) || s.name.toLowerCase().startsWith(q));
  const contains = skills.filter(
    (s) =>
      !starts.includes(s) &&
      (s.slug.includes(q) || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)),
  );
  return [...starts, ...contains];
}
