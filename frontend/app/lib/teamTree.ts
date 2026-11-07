/**
 * Teams as a tree: one parent each, at most six teams from the top down, no cycles (the backend checks
 * every change again and answers `team_cycle`, `team_too_deep` or `team_archived`). The tree is for usage
 * and, later, budgets; rights do not flow down it -- each team keeps its own roles.
 *
 * Everything here works on the flat list the teams API returns: `parent_id` per team, null at the top.
 * A team whose parent is missing from the list (archived and filtered out, or not visible) counts as a
 * top team, and a broken chain never loops: each team is visited once.
 */

/** The most teams on one path, the top team counting as level 1. */
export const MAX_TEAM_DEPTH = 6;

export interface TreeTeam {
  id: string;
  name: string;
  parent_id?: string | null;
  archived_at?: string | null;
}

export interface TreeRow<T> {
  team: T;
  /** 1 for a top team. */
  depth: number;
  /** Whether other teams of the list hang below it. */
  hasChildren: boolean;
}

function childrenOf<T extends TreeTeam>(teams: T[]): Map<string | null, T[]> {
  const ids = new Set(teams.map((t) => t.id));
  const children = new Map<string | null, T[]>();
  for (const team of teams) {
    const parent = team.parent_id && ids.has(team.parent_id) && team.parent_id !== team.id ? team.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), team]);
  }
  return children;
}

/** The teams depth first, each team's sub-teams right below it, siblings by name. */
export function asTree<T extends TreeTeam>(teams: T[], lang?: string): TreeRow<T>[] {
  const collator = new Intl.Collator(lang, { sensitivity: "base", numeric: true });
  const children = childrenOf(teams);
  const rows: TreeRow<T>[] = [];
  const seen = new Set<string>();
  const visit = (team: T, depth: number) => {
    if (seen.has(team.id)) return;
    seen.add(team.id);
    const below = [...(children.get(team.id) ?? [])].sort((a, b) => collator.compare(a.name, b.name));
    rows.push({ team, depth, hasChildren: below.length > 0 });
    for (const child of below) visit(child, depth + 1);
  };
  const roots = [...(children.get(null) ?? [])].sort((a, b) => collator.compare(a.name, b.name));
  for (const root of roots) visit(root, 1);
  // A cycle in the data (which the backend refuses) leaves teams no root reaches: list them at the top.
  for (const team of teams) if (!seen.has(team.id)) visit(team, 1);
  return rows;
}

/** The ids of every team below `id`, at any depth. */
export function descendantsOf(teams: TreeTeam[], id: string): Set<string> {
  const children = childrenOf(teams);
  const found = new Set<string>();
  const stack = [...(children.get(id) ?? [])];
  while (stack.length) {
    const team = stack.pop()!;
    if (found.has(team.id) || team.id === id) continue;
    found.add(team.id);
    stack.push(...(children.get(team.id) ?? []));
  }
  return found;
}

/** A team's level: 1 at the top. */
export function depthOf(teams: TreeTeam[], id: string): number {
  const byId = new Map(teams.map((t) => [t.id, t]));
  let depth = 0;
  const seen = new Set<string>();
  for (let team = byId.get(id); team && !seen.has(team.id); team = team.parent_id ? byId.get(team.parent_id) : undefined) {
    seen.add(team.id);
    depth += 1;
  }
  return Math.max(depth, 1);
}

/** How many levels a team and its sub-teams take: 1 for a team with none. */
export function heightOf(teams: TreeTeam[], id: string): number {
  const children = childrenOf(teams);
  const seen = new Set<string>();
  const height = (teamId: string): number => {
    if (seen.has(teamId)) return 0;
    seen.add(teamId);
    const below = children.get(teamId) ?? [];
    return 1 + Math.max(0, ...below.map((c) => height(c.id)));
  };
  return height(id);
}

export interface ParentOption<T> {
  team: T;
  depth: number;
  /** False when the team, with its sub-teams, would end up more than six levels down. */
  allowed: boolean;
  /** The team's current parent, archived: listed so it can stay, though nothing new hangs below it. */
  archived: boolean;
}

/**
 * The teams `editing` (null: a new team) may hang below: never itself or one of its own sub-teams (a
 * cycle), never an archived team -- except its current parent, which it keeps (the backend checks only a
 * move); those that would push it or its sub-teams past six levels are listed but not allowed, so the
 * reason can be shown.
 */
export function parentOptions<T extends TreeTeam>(teams: T[], editing: string | null, lang?: string): ParentOption<T>[] {
  const below = editing ? descendantsOf(teams, editing) : new Set<string>();
  const height = editing ? heightOf(teams, editing) : 1;
  const current = editing ? (teams.find((t) => t.id === editing)?.parent_id ?? null) : null;
  return asTree(teams, lang)
    .filter(({ team }) => team.id !== editing && !below.has(team.id) && (!team.archived_at || team.id === current))
    .map(({ team, depth }) => ({
      team,
      depth,
      allowed: team.id === current || depth + height <= MAX_TEAM_DEPTH,
      archived: Boolean(team.archived_at),
    }));
}

/**
 * The active teams and, so that none of them loses its place, every archived team above one of them: a
 * sub-team of an archived team stays below it (shown as archived) instead of moving to the top.
 */
export function activeWithAncestors<T extends TreeTeam>(teams: T[]): T[] {
  const byId = new Map(teams.map((t) => [t.id, t]));
  const keep = new Set<string>();
  for (const team of teams) {
    if (team.archived_at) continue;
    for (let at: T | undefined = team; at && !keep.has(at.id); at = at.parent_id ? byId.get(at.parent_id) : undefined) {
      keep.add(at.id);
    }
  }
  return teams.filter((t) => keep.has(t.id));
}

/** Teams that can be picked (a home team, a team to look at): active ones, as a tree. */
export function pickableTeams<T extends TreeTeam>(teams: T[], lang?: string): TreeRow<T>[] {
  return asTree(teams.filter((t) => !t.archived_at), lang);
}
