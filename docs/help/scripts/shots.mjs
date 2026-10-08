// Screenshots for the Help pages, taken from a running Fadenstack install, read-only: nothing is created,
// confirmed or changed. Before each picture every lab name, address and person on the page is swapped for the
// neutral examples of STYLE.md, so no real install shows in a public page.
//
//   HELP_SHOTS_BASE=https://console.example     the install
//   HELP_SHOTS_COOKIE_FILE=path                 a signed-in admin's `fapiauth` cookie value
//   HELP_SHOTS_MAP=path                         JSON [[from, to], ...]: the install's own names (kept outside
//                                               the repo, since they are what must not be published)
//   HELP_SHOTS_IDS=path                         JSON {"cluster": id, "deployment": id, "machine": id, "worker": name}
//   HELP_SHOTS_LANG=de                          the console's language (default en); German pictures go to
//                                               pages/images/de/, and the German Help shows them (build.py)
//   PLAYWRIGHT=path                             Playwright's index.mjs (default: frontend/node_modules)
//   node docs/help/scripts/shots.mjs [name ...]  -> docs/help/pages/images/<name>.png
//
// Any private (or 100.64/10) IPv4 address the map does not name becomes a 192.0.2.x one. Check every picture before you commit
// it: the swap covers text, not pictures inside pictures.

import { mkdirSync, readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../../..");
const LANG = process.env.HELP_SHOTS_LANG || "en";
// HELP_SHOTS_OUT puts the pictures elsewhere, to look at them before they replace the ones in the pages.
const OUT = process.env.HELP_SHOTS_OUT || resolve(HERE, LANG === "en" ? "../pages/images" : `../pages/images/${LANG}`);

/** The console's own text in LANG for an English label, from backend/i18n (the label itself if there is none). */
const L = (() => {
  if (LANG === "en") return (text) => text;
  const flat = (dir) => {
    const out = {};
    const walk = (obj, path) => {
      for (const [k, v] of Object.entries(obj)) {
        if (v && typeof v === "object") walk(v, `${path}${k}.`);
        else out[`${path}${k}`] = String(v);
      }
    };
    for (const f of readdirSync(dir).filter((n) => n.endsWith(".json"))) walk(JSON.parse(readFileSync(resolve(dir, f), "utf-8")), `${f}:`);
    return out;
  };
  const en = flat(resolve(REPO, "backend/i18n/en"));
  const other = flat(resolve(REPO, `backend/i18n/${LANG}`));
  const byText = new Map();
  for (const [key, text] of Object.entries(en)) if (!byText.has(text) && other[key]) byText.set(text, other[key]);
  return (text) => byText.get(text) ?? text;
})();
const env = (name) => {
  const v = process.env[name];
  if (!v) throw new Error(`set ${name}`);
  return v;
};
const BASE = env("HELP_SHOTS_BASE").replace(/\/$/, "");
const COOKIE = readFileSync(env("HELP_SHOTS_COOKIE_FILE"), "utf-8").trim();
const MAP = JSON.parse(readFileSync(env("HELP_SHOTS_MAP"), "utf-8"));
const IDS = JSON.parse(readFileSync(env("HELP_SHOTS_IDS"), "utf-8"));
const pw = await import(pathToFileURL(process.env.PLAYWRIGHT ?? resolve(REPO, "frontend/node_modules/playwright/index.mjs")).href);

/** Runs in the page: swap text in place, now and whenever React writes it again. */
function neutral(map) {
  // Private ranges, and 100.64/10 (carrier-grade NAT, which VPN overlays such as Tailscale use).
  const ip = /\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b/g;
  const seen = new Map();
  const fix = (s) => {
    if (!s) return s;
    let out = s;
    for (const [from, to] of map) out = out.split(from).join(to);
    return out.replace(ip, (a) => {
      if (!seen.has(a)) seen.set(a, `192.0.2.${30 + seen.size}`);
      return seen.get(a);
    });
  };
  const attrs = ["title", "aria-label", "placeholder", "value", "href"];
  const fixNode = (n) => {
    if (n.nodeType === 3) {
      const v = fix(n.nodeValue);
      if (v !== n.nodeValue) n.nodeValue = v;
      return;
    }
    if (n.nodeType !== 1) return;
    for (const a of attrs) {
      if (n.hasAttribute?.(a)) {
        const v = fix(n.getAttribute(a));
        if (v !== n.getAttribute(a)) n.setAttribute(a, v);
      }
    }
    if ((n.tagName === "INPUT" || n.tagName === "TEXTAREA") && fix(n.value) !== n.value) n.value = fix(n.value);
    const w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let c;
    while ((c = w.nextNode())) if (c !== n) fixNode.shallow(c);
  };
  fixNode.shallow = (c) => {
    if (c.nodeType === 3) {
      const v = fix(c.nodeValue);
      if (v !== c.nodeValue) c.nodeValue = v;
    } else {
      for (const a of attrs) {
        if (c.hasAttribute?.(a)) {
          const v = fix(c.getAttribute(a));
          if (v !== c.getAttribute(a)) c.setAttribute(a, v);
        }
      }
      if ((c.tagName === "INPUT" || c.tagName === "TEXTAREA") && fix(c.value) !== c.value) c.value = fix(c.value);
    }
  };
  const all = () => document.body && fixNode(document.body);
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") fixNode(r.target);
      else if (r.type === "attributes") fixNode.shallow(r.target);
      else r.addedNodes.forEach(fixNode);
    }
  }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: attrs });
  document.addEventListener("DOMContentLoaded", all);
  window.__neutral = all;
  // The tab title is not on the page, but browsers show it in some pictures.
  setInterval(() => {
    const t = fix(document.title);
    if (t !== document.title) document.title = t;
  }, 200);
}

const settle = async (page, ms = 1500) => {
  await page.waitForLoadState("networkidle").catch(() => {});
  // Pages that load their table after the first paint show a spinner meanwhile.
  await page.locator('[role="progressbar"]').first().waitFor({ state: "hidden", timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(ms);
  await page.evaluate(() => window.__neutral?.());
};

// name: the file under pages/images/; path: where to go; act: what to open first (never a confirm button);
// clip: a locator to picture instead of the whole window; region: a fixed part of the window.
const SHOTS = [
  // Use
  { name: "use/chat", path: "/chat" },
  { name: "use/chat-model", path: "/chat", act: async (p) => p.getByRole("combobox").first().click(), clip: (p) => p.getByRole("listbox") },
  {
    name: "use/chat-skills", path: "/chat", act: async (p) => p.getByRole("button", { name: L("Skills") }).first().click(),
    region: { x: 460, y: 420, width: 800, height: 480 },
  },
  { name: "use/skills-page", path: "/skills" },
  { name: "use/profile", path: "/admin/profile" },
  // Administer
  { name: "administer/dashboard", path: "/admin/dashboard" },
  { name: "administer/users", path: "/admin/users" },
  { name: "administer/roles", path: "/admin/roles" },
  { name: "administer/marketplace", path: "/admin/marketplace" },
  { name: "administer/deployments", path: "/admin/deployments" },
  { name: "administer/deployment", path: () => `/admin/deployments/${IDS.deployment}` },
  {
    name: "administer/deployment-scale", path: () => `/admin/deployments/${IDS.deployment}`,
    act: async (p) => p.getByText(L("Scale"), { exact: true }).first().click(), clip: (p) => p.getByRole("dialog"),
  },
  { name: "administer/providers", path: "/admin/llm/providers" },
  { name: "administer/mcp-servers", path: "/admin/mcp/servers" },
  { name: "administer/skills", path: "/admin/skills" },
  { name: "administer/agents", path: "/admin/agents" },
  { name: "administer/pii-policies", path: "/admin/pii/policies" },
  { name: "administer/governance", path: "/admin/governance" },
  { name: "administer/data-residency", path: "/admin/security-map" },
  { name: "administer/observability", path: "/admin/observability" },
  // Operate
  { name: "operate/machines", path: "/admin/nodes" },
  { name: "operate/machine", path: () => `/admin/nodes/${IDS.machine}` },
  { name: "operate/clusters", path: "/admin/clusters" },
  { name: "operate/cluster", path: () => `/admin/clusters/${IDS.cluster}` },
  {
    name: "operate/cluster-add-machines", path: () => `/admin/clusters/${IDS.cluster}`,
    act: async (p) => p.getByText(L("Add machines"), { exact: true }).first().click(), clip: (p) => p.getByRole("dialog"),
  },
  {
    name: "operate/cluster-drain-plan", path: () => `/admin/clusters/${IDS.cluster}`,
    act: async (p) => {
      await p.getByRole("button", { name: new RegExp(`${IDS.worker}, ${L("worker")}`) }).click();
      await p.getByRole("button", { name: L("Drain"), exact: true }).click();
      await p.locator("[data-drain-confirm]").waitFor({ timeout: 120000 });
    },
    clip: (p) => p.getByRole("dialog"),
  },
  { name: "operate/containers", path: "/admin/containers" },
  { name: "operate/logs", path: "/admin/logs" },
  { name: "operate/settings", path: "/admin/settings" },

  // More of the same screens: dialogs opened and steps walked, never confirmed.
  {
    name: "use/chat-tools-panel", path: "/chat",
    act: async (p) => {
      await p.getByRole("button", { name: L("Tools") }).first().click();
      // The tool list loads after the panel opens; placeholders stand in meanwhile.
      await p.locator(".MuiSkeleton-root").first().waitFor({ state: "hidden", timeout: 45000 }).catch(() => {});
    },
    region: { x: 1120, y: 56, width: 320, height: 844 },
  },
  {
    name: "use/chat-privacy-tab", path: "/chat",
    act: async (p) => {
      await p.getByRole("button", { name: L("Tools") }).first().click();
      await p.getByRole("tab", { name: L("Privacy") }).click();
    },
    region: { x: 1120, y: 56, width: 320, height: 844 },
  },
  { name: "administer/users-create", path: "/admin/users", act: async (p) => p.getByRole("button", { name: L("Create User") }).click(), clip: (p) => p.getByRole("dialog") },
  { name: "administer/users-edit-roles", path: "/admin/users", act: async (p) => p.getByRole("button", { name: L("Edit Roles") }).first().click(), clip: (p) => p.getByRole("dialog") },
  { name: "administer/users-developer-mode", path: "/admin/users", act: async (p) => p.getByRole("button", { name: L("Developer mode") }).first().click(), clip: (p) => p.getByRole("dialog") },
  { name: "administer/skill-sharing", path: () => `/admin/skills/${IDS.skill}`, act: async (p) => p.getByRole("tab", { name: L("Sharing") }).click() },
  { name: "administer/skill-versions", path: () => `/admin/skills/${IDS.skill}`, act: async (p) => p.getByRole("tab", { name: L("Versions") }).click() },
  {
    name: "administer/agents-list", path: "/admin/agents",
    act: async (p) => p.getByText(L("Word assistant")).first().waitFor({ timeout: 45000 }),
  },
  ...["Definition", "Versions", "Apps", "Access", "Activity"].map((tab) => ({
    name: `administer/agent-${tab.toLowerCase()}`, path: () => `/admin/agents/${IDS.agent}`,
    act: async (p) => p.getByRole("tab", { name: L(tab) }).click(),
  })),
  {
    name: "administer/provider-add", path: "/admin/llm/providers", clip: (p) => p.getByRole("dialog"),
    act: async (p) => {
      await p.getByRole("button", { name: L("Add Provider") }).click();
      await p.getByRole("dialog").getByRole("combobox").first().click({ force: true });
      await p.getByRole("option", { name: /remote/i }).first().click({ force: true });
    },
  },
  { name: "administer/provider-set-price", path: "/admin/llm/providers", act: async (p) => p.getByRole("button", { name: L("Set price") }).first().click({ force: true }), clip: (p) => p.getByRole("dialog") },
  { name: "administer/marketplace-model", path: "/admin/marketplace", act: async (p) => p.getByRole("button", { name: /Qwen3\.8-27B-FP8/ }).first().click({ force: true }) },
  { name: "administer/marketplace-on-server", path: "/admin/marketplace", act: async (p) => p.getByRole("tab", { name: L("On this server") }).click() },
  { name: "administer/marketplace-hf-token", path: "/admin/marketplace", act: async (p) => p.getByText(/^Hugging Face:/).first().click(), clip: (p) => p.getByRole("dialog") },
  { name: "administer/host-where", path: "/admin/marketplace", act: async (p) => p.getByRole("button", { name: L("Host"), exact: true }).first().click(), clip: (p) => p.getByRole("dialog") },
  {
    name: "administer/host-engine", path: "/admin/marketplace", clip: (p) => p.getByRole("dialog"),
    act: async (p) => {
      await p.getByRole("button", { name: L("Host"), exact: true }).first().click();
      await settle(p, 1500);
      await p.getByRole("dialog").getByRole("button", { name: L("Next") }).click();
    },
  },
  {
    name: "administer/host-review", path: "/admin/marketplace", clip: (p) => p.getByRole("dialog"),
    act: async (p) => {
      await p.getByRole("button", { name: L("Host"), exact: true }).first().click();
      await settle(p, 1500);
      await p.getByRole("dialog").getByRole("button", { name: L("Next") }).click();
      await settle(p, 1000);
      await p.getByRole("dialog").getByRole("button", { name: L("Next") }).click();
    },
  },
  { name: "administer/deployment-logs", path: () => `/admin/deployments/${IDS.deployment}`, act: async (p) => p.getByText(L("Logs"), { exact: true }).last().scrollIntoViewIfNeeded() },
  { name: "administer/governance-add-rule", path: "/admin/governance", act: async (p) => p.getByRole("button", { name: L("Add rule") }).click(), clip: (p) => p.getByRole("dialog") },
  { name: "administer/governance-classes", path: "/admin/governance", act: async (p) => p.getByRole("tab", { name: L("Tool classes") }).click() },
  { name: "administer/governance-decisions", path: "/admin/governance", act: async (p) => p.getByRole("tab", { name: L("Decisions") }).click() },
  { name: "administer/cost-pricing", path: "/admin/observability", act: async (p) => p.getByRole("tab", { name: L("Pricing") }).click() },
  { name: "operate/add-machine", path: "/admin/nodes?add=1", clip: (p) => p.getByRole("dialog") },
  { name: "operate/cluster-create-name", path: "/admin/clusters", act: async (p) => p.getByText(L("Create a cluster"), { exact: true }).first().click(), clip: (p) => p.getByRole("dialog") },
  {
    name: "operate/cluster-create-machines", path: "/admin/clusters", clip: (p) => p.getByRole("dialog"),
    act: async (p) => {
      await p.getByText(L("Create a cluster"), { exact: true }).first().click();
      await p.getByRole("dialog").locator("input").first().fill("gpu-room");
      await p.getByRole("dialog").getByRole("button", { name: L("Next") }).click({ force: true });
    },
  },
  {
    name: "operate/cluster-machine-selected", path: () => `/admin/clusters/${IDS.cluster}`,
    act: async (p) => p.getByRole("button", { name: new RegExp(`${IDS.worker}, ${L("worker")}`) }).click(),
  },
  { name: "operate/cluster-runtime", path: () => `/admin/clusters/${IDS.cluster}`, act: async (p) => p.getByRole("button", { name: L("Switch"), exact: true }).scrollIntoViewIfNeeded() },
  {
    name: "operate/cluster-advanced", path: () => `/admin/clusters/${IDS.cluster}`,
    act: async (p) => {
      await p.getByRole("button", { name: L("Advanced") }).click();
      await p.getByRole("button", { name: L("Advanced") }).scrollIntoViewIfNeeded();
    },
  },
  { name: "operate/images", path: "/admin/images", region: { x: 240, y: 0, width: 1200, height: 560 } },
  { name: "operate/container-new", path: "/admin/containers/new" },
  { name: "operate/container", path: () => `/admin/containers/${IDS.container}` },
  { name: "operate/container-edit", path: () => `/admin/containers/${IDS.container}/edit` },

  // Fewer words, more pictures: the parts of a screen a step talks about.
  { name: "use/chat-composer", path: "/chat", region: { x: 452, y: 826, width: 796, height: 70 } },
  { name: "use/chat-sidebar", path: "/chat", region: { x: 0, y: 0, width: 260, height: 460 } },
  {
    name: "use/chat-reply-details", path: "/chat",
    act: async (p) => {
      // A chat with an answer: chats in the sidebar are buttons named by their title.
      await p.getByRole("button", { name: /^Explain quantum computing/ }).first().click();
      await settle(p, 2000);
    },
    region: { x: 260, y: 0, width: 1180, height: 900 },
  },
  { name: "use/chat-phone", path: "/chat", viewport: { width: 390, height: 844 }, wait: 5000 },
  {
    name: "administer/groups", path: "/admin/groups", clip: (p) => p.getByRole("dialog"),
    act: async (p) => p.getByRole("button", { name: L("Create Group") }).first().click(),
  },
  {
    name: "administer/user-api-tokens", path: "/admin/users", clip: (p) => p.getByRole("dialog"),
    act: async (p) => p.getByRole("button", { name: L("API tokens") }).first().click(),
  },
  {
    name: "administer/user-suspend", path: "/admin/users", clip: (p) => p.getByRole("dialog"),
    act: async (p) => p.getByRole("button", { name: L("Suspend") }).last().click(),
  },
  {
    name: "administer/settings-sign-in", path: "/admin/settings",
    act: async (p) => p.getByText(L("Sign-in lifetime (s)")).first().scrollIntoViewIfNeeded(),
    // The card that holds the sign-in and API token lifetimes.
    clip: (p) => p.getByText(L("Sign-in lifetime (s)")).first().locator("xpath=ancestor::div[contains(@class,'MuiPaper-root')][1]"),
  },
];

const only = new Set(process.argv.slice(2));
const host = new URL(BASE).hostname;
const browser = await pw.chromium.launch();
const context = await browser.newContext({
  ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 }, locale: LANG === "de" ? "de-DE" : "en-US", colorScheme: "dark",
});
await context.addCookies([{ name: "fapiauth", value: COOKIE, url: BASE }]);
await context.addInitScript((lang) => {
  try { localStorage.setItem("i18nextLng", lang); } catch {}
}, LANG);
await context.addInitScript(neutral, [...MAP, [host, "ai.example.internal"]]);

let failed = 0;
for (const shot of SHOTS) {
  if (only.size && !only.has(shot.name)) continue;
  const page = await context.newPage();
  if (shot.viewport) await page.setViewportSize(shot.viewport);
  try {
    const path = typeof shot.path === "function" ? shot.path() : shot.path;
    await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
    await settle(page);
    if (shot.wait) await page.waitForTimeout(shot.wait);
    if (shot.act) {
      await shot.act(page);
      await settle(page, 1000);
    }
    const file = resolve(OUT, `${shot.name}.png`);
    mkdirSync(dirname(file), { recursive: true });
    if (shot.clip) await shot.clip(page).first().screenshot({ path: file });
    else if (shot.region) await page.screenshot({ path: file, clip: shot.region });
    else await page.screenshot({ path: file });
    console.log("ok", shot.name);
  } catch (err) {
    failed += 1;
    console.log("FAILED", shot.name, String(err).split("\n")[0]);
  } finally {
    await page.close();
  }
}
await browser.close();
process.exit(failed ? 1 : 0);
