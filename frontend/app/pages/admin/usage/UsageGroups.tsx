/**
 * The groups of the Usage page: by agent, model or provider a flat list; by team the tree.
 *
 * Every row covers at least k people or shows no figures (M04, M05). The order is by name, never by
 * usage (M11): named rows first, then the row without a key ("Unassigned", "No agent", "Unknown
 * model"), then the merged row of the groups below k.
 *
 * By team, a team's figures include its sub-teams. What no shown sub-team holds -- the sub-teams too
 * small to show alone and the people directly in the team -- is one "smaller teams and others" row below
 * it; people without a usage group are "Unassigned", at the top level. Rows open and close per team; all
 * start closed, so a large organisation reads from the top down.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

import { isHidden, type AdminUsage, type AdminUsageGroup, type GroupBy } from "~/api/usage";
import { fmtInt } from "~/components/dashboard/format";
import { byLabel, MeasuresTable, type MeasuresRow } from "~/components/usage/UsageParts";

type T = (key: string, options?: Record<string, unknown>) => string;

/** What a row without a key stands for, per grouping. */
const NONE_KEY: Record<GroupBy, string> = {
  team: "usage.admin.unassigned",
  agent: "usage.admin.no_agent",
  model: "usage.admin.unknown_model",
  provider: "usage.admin.unknown_provider",
};

/** A row's people: the number, or (below k, or a merged row) what is known of it. */
function peopleCell(g: AdminUsageGroup, k: number, lang: string, t: T): string {
  if (g.people != null) return fmtInt(g.people, lang);
  return g.other && !isHidden(g) ? t("usage.admin.other_people", { k }) : t("usage.admin.people_below_k", { k });
}

/** A row's name, with a line under it for the rows that are not one group. */
function Name({ title, hint }: { title: string; hint?: string }) {
  return (
    <Stack component="span" sx={{ minWidth: 0 }}>
      <Typography component="span" variant="body2" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {hint && (
        <Typography component="span" variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </Stack>
  );
}

export default function UsageGroups({ data }: { data: AdminUsage }) {
  return data.group_by === "team" ? <TeamTree data={data} /> : <FlatGroups data={data} />;
}

function FlatGroups({ data }: { data: AdminUsage }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const k = data.min_group_size;
  const named = data.groups.filter((g) => !g.other && g.key !== null);
  const none = data.groups.filter((g) => !g.other && g.key === null);
  const merged = data.groups.filter((g) => g.other);
  const nameOf = (g: AdminUsageGroup) => g.label ?? g.key ?? t(NONE_KEY[data.group_by]);

  const rows: MeasuresRow[] = [
    ...byLabel(named, nameOf, lang).map((g) => ({
      key: g.key!,
      label: nameOf(g),
      lead: [peopleCell(g, k, lang, t)],
      measures: g.suppressed ? null : g,
      hidden: t("usage.admin.suppressed", { k }),
    })),
    ...none.map((g, i) => ({
      key: `none-${i}`,
      label: <Name title={t(NONE_KEY[data.group_by])} />,
      lead: [peopleCell(g, k, lang, t)],
      measures: g,
      hidden: t("usage.admin.suppressed", { k }),
    })),
    ...merged.map((g, i) => ({
      key: `other-${i}`,
      label: <Name title={t("usage.admin.other_row", { k })} hint={t("usage.admin.other_row_hint", { k })} />,
      lead: [peopleCell(g, k, lang, t)],
      measures: g,
      hidden: t("usage.admin.suppressed", { k }),
    })),
  ];

  return (
    <MeasuresTable
      ariaLabel={t(`usage.admin.group_title.${data.group_by}`)}
      labelHeader={t(`usage.admin.group_by.${data.group_by}`)}
      leadHeaders={[t("usage.measures.people")]}
      rows={rows}
    />
  );
}

interface TeamNode {
  id: string;
  kind: "team" | "smaller" | "unassigned";
  group: AdminUsageGroup;
  children: TeamNode[];
  /** The team a "smaller teams and others" row belongs to; none at the top. */
  parentLabel?: string | null;
}

/**
 * A row's path, top first. The server sends it (a team's ends with the team; the merged row carries its
 * parent's, `[]` at the top; Unassigned `[]`); built from the keys when it does not.
 */
function pathOf(g: AdminUsageGroup): string[] {
  if (g.path) return g.path;
  if (g.key) return g.parent_key ? [g.parent_key, g.key] : [g.key];
  return g.other && g.parent_key ? [g.parent_key] : [];
}

/**
 * The rows as a tree, by `path` rather than `key`: as recorded, a team moved since appears once per path
 * it had. A team below the team its path names; the merged row ("smaller teams and others": the smaller
 * sub-teams and the people directly in the team) below that team, or at the top; "Unassigned" at the top.
 * A row whose parent is not shown counts as a top row.
 */
export function teamTree(groups: AdminUsageGroup[], lang?: string): TeamNode[] {
  const collator = new Intl.Collator(lang, { sensitivity: "base", numeric: true });
  const teamId = (path: string[]) => `t:${path.join("/")}`;
  const nodes = groups.map((group) => {
    const path = pathOf(group);
    const kind: TeamNode["kind"] = group.key ? "team" : group.other ? "smaller" : "unassigned";
    const parent = kind === "team" ? path.slice(0, -1) : kind === "smaller" ? path : [];
    return {
      node: {
        id: kind === "team" ? teamId(path) : kind === "smaller" ? `o:${path.join("/")}` : "unassigned",
        kind,
        group,
        children: [],
      } as TeamNode,
      parentId: parent.length ? teamId(parent) : null,
    };
  });
  const teams = new Map(nodes.filter((n) => n.node.kind === "team").map((n) => [n.node.id, n.node]));
  const roots: TeamNode[] = [];
  for (const { node, parentId } of nodes) {
    const parent = parentId ? teams.get(parentId) : undefined;
    if (parent && parent !== node) {
      if (node.kind === "smaller") node.parentLabel = parent.group.label;
      parent.children.push(node);
    } else roots.push(node);
  }
  const rank = { team: 0, smaller: 1, unassigned: 2 } as const;
  const order = (list: TeamNode[]) => {
    list.sort(
      (a, b) =>
        rank[a.kind] - rank[b.kind] || collator.compare(a.group.label ?? a.id, b.group.label ?? b.id),
    );
    for (const node of list) order(node.children);
  };
  order(roots);
  return roots;
}

function TeamTree({ data }: { data: AdminUsage }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const k = data.min_group_size;
  const roots = useMemo(() => teamTree(data.groups, lang), [data.groups, lang]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const withChildren = useMemo(() => {
    const ids: string[] = [];
    const walk = (list: TeamNode[]) => list.forEach((n) => n.children.length && (ids.push(n.id), walk(n.children)));
    walk(roots);
    return ids;
  }, [roots]);

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rows: MeasuresRow[] = [];
  const visit = (node: TeamNode, depth: number) => {
    const g = node.group;
    // In today's structure a team deleted since stands at the top without a name.
    const title =
      node.kind === "team"
        ? (g.label ?? t("usage.admin.deleted_team"))
        : node.kind === "smaller"
          ? t("usage.admin.smaller_teams")
          : t("usage.admin.unassigned");
    const hint =
      node.kind === "smaller"
        ? node.parentLabel
          ? t("usage.admin.smaller_teams_hint", { name: node.parentLabel })
          : t("usage.admin.smaller_teams_top_hint", { k })
        : node.kind === "unassigned"
          ? t("usage.admin.unassigned_hint")
          : undefined;
    const isOpen = open.has(node.id);
    rows.push({
      key: node.id,
      label: (
        <Stack direction="row" alignItems="center" gap={0.5} sx={{ pl: (depth - 1) * 2.5 }} data-usage-node={node.id} data-usage-depth={depth}>
          {node.children.length > 0 ? (
            <IconButton
              size="small"
              onClick={() => toggle(node.id)}
              aria-expanded={isOpen}
              aria-label={t(isOpen ? "usage.admin.collapse" : "usage.admin.expand", { name: title })}
            >
              {isOpen ? <ExpandMoreIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />}
            </IconButton>
          ) : (
            <Box aria-hidden sx={{ width: 34, flexShrink: 0 }} />
          )}
          <Name title={title} hint={hint} />
        </Stack>
      ),
      lead: [peopleCell(g, k, lang, t)],
      measures: g.suppressed && node.kind === "team" ? null : g,
      hidden: t("usage.admin.suppressed", { k }),
    });
    if (isOpen) for (const child of node.children) visit(child, depth + 1);
  };
  for (const root of roots) visit(root, 1);

  const allOpen = withChildren.length > 0 && withChildren.every((id) => open.has(id));
  return (
    <Stack gap={1}>
      {withChildren.length > 0 && (
        <Box>
          <Button size="small" onClick={() => setOpen(allOpen ? new Set() : new Set(withChildren))}>
            {allOpen ? t("usage.admin.collapse_all") : t("usage.admin.expand_all")}
          </Button>
        </Box>
      )}
      <MeasuresTable
        ariaLabel={t("usage.admin.group_title.team")}
        labelHeader={t("usage.admin.group_by.team")}
        leadHeaders={[t("usage.measures.people")]}
        rows={rows}
      />
    </Stack>
  );
}
