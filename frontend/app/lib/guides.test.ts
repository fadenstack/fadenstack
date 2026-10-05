/**
 * The guides, the getting-started list and the page help point at real things.
 *
 * The old guides broke silently: steps aimed at elements that had been renamed
 * or removed, texts went missing, and new pages had no help at all. A guide
 * whose target is gone skips that step, so nobody notices until someone follows
 * it. These checks catch it when the code changes instead.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { CHECKLIST } from "~/lib/gettingStarted";
import { getGuide, guideTextKeys, listGuides } from "~/lib/guides";
import { allPageHelp, resolvePageHelp } from "~/lib/pageHelp";

// Vitest runs from the frontend's root.
const APP = resolve("app");
const I18N = resolve("../backend/i18n");

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    if (typeof value === "string") out[prefix + key] = value;
    else Object.assign(out, flatten(value, `${prefix}${key}.`));
  }
  return out;
}

const tour = flatten(JSON.parse(readFileSync(join(I18N, "en", "tour.json"), "utf-8")) as Tree);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const code = sources(APP).map((f) => readFileSync(f, "utf-8"));

/** Every data-tour-id the code sets, with the sidebar's templated ones expanded. */
function tourIds(): Set<string> {
  const ids = new Set<string>();
  for (const text of code) {
    for (const m of text.matchAll(/data-tour-id=(?:"([^"]+)"|\{\s*["`]([^"`$]+)["`]\s*\})/g)) ids.add(m[1] ?? m[2]);
  }
  const nav = readFileSync(join(APP, "lib", "adminConstants.tsx"), "utf-8");
  for (const m of nav.matchAll(/^\s+id: "([^"]+)"/gm)) ids.add(`sidebar.${m[1]}`);
  return ids;
}

describe("guides", () => {
  const ids = tourIds();

  it("point every step at an element the code renders", () => {
    const missing: string[] = [];
    for (const guide of listGuides()) {
      for (const step of guide.steps) {
        const id = /data-tour-id='([^']+)'/.exec(step.target)?.[1];
        if (!id || !ids.has(id)) missing.push(`${guide.id}.${step.id}: ${step.target}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("have every text in English", () => {
    expect(guideTextKeys().filter((key) => !(key in tour))).toEqual([]);
  });

  it("have unique ids, and steps that can end", () => {
    const guideIds = listGuides().map((g) => g.id);
    expect(new Set(guideIds).size).toBe(guideIds.length);
    for (const guide of listGuides()) {
      const stepIds = guide.steps.map((s) => s.id);
      expect(new Set(stepIds).size, guide.id).toBe(stepIds.length);
      // An action step moves on when the next step's target appears: the last has none.
      expect(guide.steps.at(-1)?.action ?? false, `${guide.id} ends on an action step`).toBe(false);
    }
  });
});

describe("getting started", () => {
  it("has every text, and only guides that exist", () => {
    const missing = CHECKLIST.flatMap((item) => [`checklist.${item.id}.title`, `checklist.${item.id}.body`]).filter(
      (key) => !(key in tour),
    );
    expect(missing).toEqual([]);
    for (const item of CHECKLIST) if (item.guide) expect(getGuide(item.guide), item.id).toBeDefined();
  });
});

describe("page help", () => {
  it("has every text, and only guides that exist", () => {
    const missing = allPageHelp()
      .flatMap((entry) => [entry.titleKey, entry.descriptionKey, ...entry.actionKeys])
      .filter((key) => !(key in tour));
    expect(missing).toEqual([]);
    for (const entry of allPageHelp()) if (entry.guide) expect(getGuide(entry.guide), entry.prefix).toBeDefined();
  });

  it("covers every admin page", () => {
    const routes = readFileSync(join(APP, "routes.ts"), "utf-8");
    const paths = [...routes.matchAll(/"(\/admin\/[^"]*)"/g)].map((m) => m[1]);
    // Pages that only send you elsewhere.
    const redirects = ["/admin/audit", "/admin/llm/models", "/admin/llm/models/:id", "/admin/llm/runtimes"];
    const uncovered = paths
      .filter((p) => !redirects.includes(p))
      .map((p) => p.replace(/:[^/]+/g, "x"))
      .filter((p) => resolvePageHelp(p) === null);
    expect(uncovered).toEqual([]);
  });
});
