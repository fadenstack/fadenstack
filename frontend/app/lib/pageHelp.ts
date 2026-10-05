/**
 * pageHelp — what the "About this page" drawer (F1) says on each admin page.
 *
 * Each entry maps a route prefix (or pattern) to texts in the "tour" namespace:
 * `page_help.<id>.title`, `.description` and `.action_<action>` for each
 * action. The first matching prefix wins, so more specific routes come before
 * their parents. `guide` names a how-to guide the drawer offers to start.
 */

export interface PageHelpEntry {
  /** i18n key for page title (tour namespace) */
  titleKey: string;
  /** i18n key for page description (tour namespace) */
  descriptionKey: string;
  /** i18n keys for bullet-point actions (tour namespace) */
  actionKeys: string[];
  /** A how-to guide for the page's main task. */
  guide?: string;
}

interface PageHelpDef {
  /** A path prefix, or a pattern for a page a prefix cannot single out. */
  prefix: string | RegExp;
  id: string;
  actions: string[];
  guide?: string;
}

/** Ordered list: most specific prefixes first. */
const PAGES: PageHelpDef[] = [
  { prefix: "/admin/dashboard", id: "dashboard", actions: ["getting_started", "attention", "customize", "status"] },

  // Containers, images, networks, stacks
  { prefix: "/admin/containers/new", id: "create_container", actions: ["image", "config", "class"] },
  { prefix: /^\/admin\/containers\/[^/]+\/edit/, id: "edit_container", actions: ["change", "save"] },
  { prefix: "/admin/containers/", id: "container_detail", actions: ["logs", "edit", "exec"] },
  { prefix: "/admin/containers", id: "containers", actions: ["filter", "control", "class", "create"] },
  { prefix: "/admin/images", id: "images", actions: ["pull", "prune"] },
  { prefix: "/admin/networks", id: "networks", actions: ["create", "inspect", "delete"] },
  { prefix: "/admin/stacks", id: "stacks", actions: ["deploy", "diff", "rollback"] },

  // Observability, logs, data residency
  { prefix: "/admin/observability", id: "observability", actions: ["overview", "requests", "pricing", "export"] },
  { prefix: "/admin/logs", id: "logs", actions: ["filter", "audit"] },
  { prefix: "/admin/security-map", id: "security_map", actions: ["residency", "where", "volume"] },

  // LLM
  { prefix: "/admin/llm/agent-trace", id: "agent_trace", actions: ["graph", "live"] },
  { prefix: "/admin/llm/endpoint", id: "endpoint", actions: ["docs", "container"] },
  { prefix: "/admin/llm/runtimes/", id: "runtime_detail", actions: ["control", "config", "logs"] },
  {
    prefix: "/admin/llm/providers",
    id: "providers",
    actions: ["add", "test", "price", "manage"],
    guide: "connect_provider",
  },

  // AI infrastructure
  { prefix: "/admin/nodes/profiles", id: "node_profiles", actions: ["create", "assign"] },
  { prefix: "/admin/nodes/", id: "node_detail", actions: ["status", "maintenance", "updates", "vllm"] },
  { prefix: "/admin/nodes", id: "nodes", actions: ["add", "status", "maintenance", "delete"], guide: "add_machine" },
  {
    prefix: "/admin/clusters/",
    id: "cluster_detail",
    actions: ["deploy", "wiring", "security", "runtime", "control"],
  },
  { prefix: "/admin/clusters", id: "clusters", actions: ["create", "open", "found"], guide: "create_cluster" },
  {
    prefix: "/admin/marketplace",
    id: "marketplace",
    actions: ["find", "fit", "host", "local", "token"],
    guide: "host_model",
  },
  { prefix: "/admin/deployments/", id: "deployment_detail", actions: ["scale", "change", "logs", "control"] },
  { prefix: "/admin/deployments", id: "deployments", actions: ["deploy", "control", "open"], guide: "host_model" },
  { prefix: "/admin/scheduler", id: "scheduler", actions: ["watch", "retry"] },

  // Access control
  {
    prefix: "/admin/users",
    id: "users",
    actions: ["create", "roles", "developer", "delete"],
    guide: "invite_people",
  },
  { prefix: "/admin/roles", id: "roles", actions: ["create", "edit"] },
  { prefix: "/admin/groups", id: "groups", actions: ["create", "members"] },
  { prefix: "/admin/auth-providers", id: "auth_providers", actions: ["add", "auto"] },

  // Privacy
  { prefix: "/admin/pii/dashboard", id: "pii_dashboard", actions: ["stats", "breakdown"] },
  { prefix: "/admin/pii/activity", id: "pii_activity", actions: ["filter"] },
  { prefix: "/admin/pii/policies", id: "pii_policies", actions: ["load", "edit", "preview"] },

  // Knowledge (RAG)
  { prefix: "/admin/rag/knowledge-base", id: "rag_knowledge_base", actions: ["upload", "describe", "retry"] },
  { prefix: "/admin/rag/documents", id: "rag_documents", actions: ["upload", "manage"] },
  { prefix: "/admin/rag/collections", id: "rag_collections", actions: ["create", "delete"] },
  { prefix: "/admin/rag/runtime", id: "rag_runtime", actions: ["view", "edit"] },
  { prefix: "/admin/rag/collectors", id: "rag_collectors", actions: ["run", "jobs"] },
  { prefix: "/admin/rag/explorer", id: "rag_explorer", actions: ["container", "upload", "publish"] },
  { prefix: "/admin/rag/publishes", id: "rag_publishes", actions: ["watch"] },
  { prefix: "/admin/rag/search", id: "rag_search", actions: ["query", "pro"] },

  // Chat workspace
  { prefix: "/admin/chat/projects", id: "chat_projects", actions: ["browse", "delete"] },
  { prefix: "/admin/chat/sessions", id: "chat_sessions", actions: ["browse", "delete"] },
  { prefix: "/admin/chat/attachments", id: "chat_attachments", actions: ["browse", "delete"] },

  // Extensions
  { prefix: "/admin/mcp/servers/", id: "mcp_detail", actions: ["tools", "test", "edit"] },
  { prefix: "/admin/mcp/servers", id: "mcp_servers", actions: ["register", "pii", "scan"] },
  { prefix: "/admin/skills/", id: "skill_detail", actions: ["edit", "share", "export"] },
  { prefix: "/admin/skills", id: "skills", actions: ["create", "publish", "views"] },

  // Agents and governance
  { prefix: "/admin/agents/", id: "agent_detail", actions: ["definition", "publish", "access", "activity"] },
  { prefix: "/admin/agents", id: "agents", actions: ["create", "open"] },
  { prefix: "/admin/governance", id: "governance", actions: ["rules", "classes", "decisions"] },

  { prefix: "/admin/settings", id: "settings", actions: ["general", "modules", "appearance"] },
  { prefix: "/admin/profile", id: "profile", actions: ["password", "tokens", "revoke"], guide: "create_api_token" },
];

function toEntry(def: PageHelpDef): PageHelpEntry {
  return {
    titleKey: `page_help.${def.id}.title`,
    descriptionKey: `page_help.${def.id}.description`,
    actionKeys: def.actions.map((a) => `page_help.${def.id}.action_${a}`),
    guide: def.guide,
  };
}

/**
 * Resolve the best-matching page help entry for the current pathname.
 * Returns `null` if no match is found.
 */
export function resolvePageHelp(pathname: string): PageHelpEntry | null {
  const def = PAGES.find((p) =>
    typeof p.prefix === "string" ? pathname.startsWith(p.prefix) : p.prefix.test(pathname),
  );
  return def ? toEntry(def) : null;
}

/** Every page's entry, for tests. */
export function allPageHelp(): (PageHelpEntry & { prefix: string })[] {
  return PAGES.map((p) => ({ prefix: String(p.prefix), ...toEntry(p) }));
}
