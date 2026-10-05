/**
 * Getting started: the steps from a fresh install to people using it, each
 * ticked from what the server actually has, not from what someone clicked.
 *
 * The first five are the main path (machine, cluster, model, chat, people);
 * the rest are for when they are needed. Each check fails quietly: a module
 * that is off, or an API this user cannot read, leaves its step unticked.
 */
import { useCallback, useEffect, useState } from "react";

import { adminUsers } from "~/api/admin";
import { agentsApi } from "~/api/agents";
import { inferenceApi } from "~/api/inference";
import { providers } from "~/api/llm";
import { listServers } from "~/api/mcp";
import { nodesApi } from "~/api/nodes";
import { listPIIPolicyTenants } from "~/api/pii";
import { preferences } from "~/api/preferences";
import { listSkills } from "~/api/skills";
import { isGuideCompleted } from "~/lib/guides";
import { GUIDES_CHANGED_EVENT } from "~/lib/guidesContext";

export type ChecklistItemId =
  | "machine"
  | "cluster"
  | "model"
  | "chat"
  | "people"
  | "provider"
  | "privacy"
  | "tools"
  | "skills"
  | "agents"
  | "token"
  | "tour";

export interface ChecklistItem {
  id: ChecklistItemId;
  /** Part of the main path, or for when it is needed. */
  main: boolean;
  /** Where the step is done. */
  route: string;
  /** A how-to guide that walks it, if there is one. */
  guide?: string;
  /** A module the step needs; the step is hidden without it. */
  module?: string;
}

export const CHECKLIST: ChecklistItem[] = [
  { id: "machine", main: true, route: "/admin/nodes", guide: "add_machine" },
  { id: "cluster", main: true, route: "/admin/clusters", guide: "create_cluster" },
  { id: "model", main: true, route: "/admin/marketplace", guide: "host_model" },
  { id: "chat", main: true, route: "/chat" },
  { id: "people", main: true, route: "/admin/users", guide: "invite_people" },
  { id: "provider", main: false, route: "/admin/llm/providers", guide: "connect_provider" },
  { id: "privacy", main: false, route: "/admin/pii/policies", module: "pii" },
  { id: "tools", main: false, route: "/admin/mcp/servers", module: "mcp" },
  { id: "skills", main: false, route: "/admin/skills", module: "skills" },
  { id: "agents", main: false, route: "/admin/agents" },
  { id: "token", main: false, route: "/admin/profile", guide: "create_api_token" },
  { id: "tour", main: false, route: "/admin/dashboard", guide: "console_tour" },
];

/** What the getting-started list keeps in the user's preferences. */
export interface GettingStartedPrefs {
  dismissed?: boolean;
  /** Opened the chat from the list: the one step the server cannot see. */
  chat_opened?: boolean;
}

export type ChecklistState = Partial<Record<ChecklistItemId, boolean>>;

async function count(fetch: () => Promise<unknown[] | { total?: number; items?: unknown[] }>): Promise<number> {
  try {
    const result = await fetch();
    if (Array.isArray(result)) return result.length;
    return result.total ?? result.items?.length ?? 0;
  } catch {
    return 0;
  }
}

/** Reads every step's state from the server. */
export async function loadChecklistState(isModuleEnabled: (m: string) => boolean): Promise<{
  state: ChecklistState;
  prefs: GettingStartedPrefs;
}> {
  const prefsRequest = preferences.get().catch(() => null);
  const [machines, clusters, deployments, users, remote, policies, tools, skills, agents, tokens] = await Promise.all([
    count(() => nodesApi.list()),
    count(() => inferenceApi.listEnvironments()),
    count(() => inferenceApi.listDeployments()),
    count(() => adminUsers.list()),
    count(async () => (await providers.list()).filter((p) => p.target === "remote_endpoint")),
    isModuleEnabled("pii") ? count(() => listPIIPolicyTenants("", 1)) : Promise.resolve(0),
    isModuleEnabled("mcp") ? count(() => listServers()) : Promise.resolve(0),
    isModuleEnabled("skills") ? count(() => listSkills({ limit: 1 })) : Promise.resolve(0),
    count(() => agentsApi.list()),
    count(() => adminUsers.listMyApiTokens()),
  ]);
  const all = ((await prefsRequest)?.preferences ?? {}) as {
    getting_started?: GettingStartedPrefs;
    tour_progress?: Record<string, { completed?: boolean } | null>;
  };
  const prefs = all.getting_started ?? {};

  return {
    prefs,
    state: {
      machine: machines > 0,
      cluster: clusters > 0,
      model: deployments > 0,
      chat: Boolean(prefs.chat_opened),
      people: users > 1,
      provider: remote > 0,
      privacy: policies > 0,
      tools: tools > 0,
      skills: skills > 0,
      agents: agents > 0,
      token: tokens > 0,
      tour: isGuideCompleted(all.tour_progress, "console_tour"),
    },
  };
}

export function visibleItems(isModuleEnabled: (m: string) => boolean): ChecklistItem[] {
  return CHECKLIST.filter((item) => !item.module || isModuleEnabled(item.module));
}

export function useGettingStarted(isModuleEnabled: (m: string) => boolean, enabled: boolean) {
  const [state, setState] = useState<ChecklistState | null>(null);
  const [prefs, setPrefs] = useState<GettingStartedPrefs>({});

  const refresh = useCallback(async () => {
    const loaded = await loadChecklistState(isModuleEnabled);
    setState(loaded.state);
    setPrefs(loaded.prefs);
  }, [isModuleEnabled]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const onChange = () => void refresh();
    window.addEventListener(GUIDES_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(GUIDES_CHANGED_EVENT, onChange);
  }, [enabled, refresh]);

  const savePrefs = useCallback(async (change: GettingStartedPrefs) => {
    setPrefs((p) => ({ ...p, ...change }));
    await preferences.update({ getting_started: change }).catch(() => {});
  }, []);

  return { state, prefs, refresh, savePrefs };
}
