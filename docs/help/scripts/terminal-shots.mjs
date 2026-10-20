// Faithful text captures from recorded CLI runs, rendered for the Help pages.
// Input must already use the neutral examples in STYLE.md. Never pass .env content.
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export async function capture(input) {
  if (!input) throw new Error("Pass the neutral CLI capture JSON path");
  const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const pw = await import(pathToFileURL(process.env.PLAYWRIGHT || resolve(repo, "frontend/node_modules/playwright/index.mjs")).href);
  const out = process.env.HELP_SHOTS_OUT || resolve(repo, "docs/help/pages/images");
  const browser = await pw.chromium.launch();
  try {
    for (const entry of JSON.parse(readFileSync(input, "utf8"))) {
      const text = `$ ${entry.command}\n\n${entry.output}`;
      if (/\b(?:10|127)\.\d+\.\d+\.\d+\b|\/home\/|C:\\Users\\|Bearer\s+\S|PRIVATE KEY/.test(text)) {
        throw new Error("CLI capture contains private host/user/credential text");
      }
      if (!/^[a-z0-9/-]+$/.test(entry.name)) throw new Error("Invalid capture name");
      const page = await browser.newPage({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 1 });
      await page.setContent('<html><meta charset="utf-8"><style>body{margin:0;background:#10151f;color:#edf2f7}pre{margin:0;padding:24px;font:18px/1.5 Consolas,"Liberation Mono",monospace;white-space:pre-wrap;overflow-wrap:anywhere;box-sizing:border-box;width:1000px}header{font:14px system-ui;color:#a9b8cd;padding:12px 24px;background:#1b2534}</style><header>Fadenstack · command line</header><pre></pre></html>');
      await page.locator("pre").evaluate((node, value) => { node.textContent = value; }, text);
      const box = await page.locator("pre").boundingBox();
      await page.setViewportSize({ width: 1000, height: Math.max(200, Math.ceil(box.y + box.height)) });
      const file = resolve(out, entry.name + ".png");
      mkdirSync(dirname(file), { recursive: true });
      await page.screenshot({ path: file, fullPage: true });
      await page.close();
      console.log("ok", entry.name);
    }
  } finally {
    await browser.close();
  }
}
