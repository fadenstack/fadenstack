/**
 * What a page does wrong while it is looked at: page errors, console errors,
 * failed API calls, untranslated keys on screen, an error screen.
 */
import { appendFileSync, mkdirSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import type { Page, Response } from "@playwright/test";

const OUT = "test-results/sweep";
mkdirSync(path.join(OUT, "pages"), { recursive: true });

/** Every dotted key of the English translations: shown as is, a translation is missing. */
const KNOWN_KEYS = (() => {
  const keys = new Set<string>();
  const walk = (node: unknown, prefix: string) => {
    if (node && typeof node === "object") {
      for (const [k, v] of Object.entries(node)) walk(v, prefix ? `${prefix}.${k}` : k);
    } else if (prefix.includes(".") || prefix.includes("_")) {
      keys.add(prefix);
    }
  };
  for (const ns of ["common", "chat", "tour"]) {
    try {
      walk(JSON.parse(readFileSync(`../backend/i18n/en/${ns}.json`, "utf-8")), "");
    } catch {
      /* a namespace that is not there */
    }
  }
  // And every key the code asks for: one missing from the files shows as itself.
  const scan = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const file = path.join(dir, name);
      if (statSync(file).isDirectory()) scan(file);
      else if (/\.tsx?$/.test(name) && !/\.test\./.test(name)) {
        for (const m of readFileSync(file, "utf-8").matchAll(/\bt\(\s*["'`]([a-z0-9_]+(?:\.[a-z0-9_]+)+)["'`]/g)) {
          keys.add(m[1]);
        }
      }
    }
  };
  scan("app");
  return keys;
})();

/** A line of text that is a translation key. Data such as audit actions ("root_mode.stop") is not. */
function looksLikeKey(line: string): boolean {
  return KNOWN_KEYS.has(line);
}

/** Console noise that is not the console's: the embedded Grafana, the browser's own resource lines. */
const NOISE = [/grafana/i, /Failed to load resource/i, /Download the React DevTools/i, /favicon/i];

/** An error screen: the router's, or the app's boundary. */
const ERROR_SCREENS = [/Unexpected Application Error/i, /Something went wrong/i, /Application error/i];

export interface Problems {
  pageErrors: string[];
  consoleErrors: string[];
  apiErrors: string[];
  apiServerErrors: string[];
  rawKeys: string[];
  errorScreen: string | null;
}

export class Watch {
  readonly problems: Problems = {
    pageErrors: [], consoleErrors: [], apiErrors: [], apiServerErrors: [], rawKeys: [], errorScreen: null,
  };

  constructor(private readonly page: Page, private readonly origin: string) {
    page.on("pageerror", (err) => {
      // The embedded Grafana's own scripts throw inside its frame (their stack says so).
      if (NOISE.some((re) => re.test(err.stack ?? ""))) return;
      this.problems.pageErrors.push(err.message.split("\n")[0]);
    });
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (NOISE.some((re) => re.test(text) || re.test(msg.location().url ?? ""))) return;
      this.problems.consoleErrors.push(text.slice(0, 300));
    });
    page.on("response", (response: Response) => this.onResponse(response));
  }

  private onResponse(response: Response) {
    const url = response.url();
    if (!url.startsWith(this.origin) || !url.includes("/api/")) return;
    const status = response.status();
    if (status < 400) return;
    const line = `${status} ${response.request().method()} ${url.slice(this.origin.length)}`;
    (status >= 500 ? this.problems.apiServerErrors : this.problems.apiErrors).push(line);
  }

  /** Look at the page once it has settled: what is on screen. */
  async inspect(): Promise<void> {
    await this.page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
    await this.page.waitForTimeout(800);
    const text = await this.page.locator("body").innerText().catch(() => "");
    // Identifiers shown as they are (translate="no": an audit action such as auth.sign_in) are data, not keys.
    const data = new Set(
      (await this.page.locator('[translate="no"]').allInnerTexts().catch(() => [] as string[])).map((l) => l.trim()),
    );
    const lines = [...new Set(text.split("\n").map((l) => l.trim()).filter(Boolean))].filter((l) => !data.has(l));
    this.problems.rawKeys.push(...lines.filter(looksLikeKey));
    this.problems.errorScreen = ERROR_SCREENS.find((re) => re.test(text))?.source ?? null;
  }

  /** Save a screenshot and the problems of *name* to the sweep's report. */
  async report(name: string, extra: Record<string, unknown> = {}): Promise<void> {
    const file = name.replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "") || "root";
    await this.page.screenshot({ path: path.join(OUT, "pages", `${file}.png`), fullPage: true }).catch(() => {});
    appendFileSync(
      path.join(OUT, "report.jsonl"),
      `${JSON.stringify({ name, url: this.page.url(), ...this.problems, ...extra })}\n`,
    );
  }

  /** Problems that fail a check: a crash, a server error, an error screen, an untranslated key. */
  failures(): string[] {
    const p = this.problems;
    return [
      ...p.pageErrors.map((e) => `page error: ${e}`),
      ...p.apiServerErrors.map((e) => `server error: ${e}`),
      ...p.rawKeys.map((k) => `untranslated: ${k}`),
      ...(p.errorScreen ? [`error screen: ${p.errorScreen}`] : []),
    ];
  }
}
