/**
 * Help's "When it does not work" table on the Mail page names every failure the console words, and the field
 * problems that need explaining, in the console's own words: an administrator searches it for the sentence on
 * screen. English and German both.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { MAIL_FAILURES } from "~/api/mailSettings";

// Vitest runs from the frontend's root.
const DOCS = resolve("../docs/help");
const I18N = resolve("../backend/i18n");

/** Field problems explained in the table too. */
const FIELD_PROBLEMS = ["m365_host", "not_certificate"] as const;

type Tree = { [key: string]: string | Tree };

function text(tree: Tree, dotted: string): string {
  let node: string | Tree = tree;
  for (const name of dotted.split(".")) node = (node as Tree)[name];
  return node as string;
}

/** What the table names a failure by: the console's sentence up to its first colon (or its full stop). */
function heading(sentence: string): string {
  const colon = sentence.indexOf(":");
  return (colon > 0 ? sentence.slice(0, colon) : sentence).replace(/\.$/, "").trim();
}

/** The first cell of each row of the table under *section*. */
function rows(page: string, section: string): string[] {
  const start = page.indexOf(section);
  expect(start, section).toBeGreaterThan(-1);
  return page
    .slice(start)
    .split("\n")
    .filter((line) => line.startsWith("| ") && !line.startsWith("|---"))
    .map((line) => line.split("|")[1].trim());
}

describe.each([
  { lang: "en", page: "pages/administer/mail.md", section: "## When it does not work" },
  { lang: "de", page: "pages-de/administer/mail.md", section: "## Wenn es nicht funktioniert" },
])("Help on mail ($lang)", ({ lang, page, section }) => {
  const words = JSON.parse(readFileSync(join(I18N, lang, "common.json"), "utf-8")) as Tree;
  const table = rows(readFileSync(join(DOCS, page), "utf-8"), section);

  it("has a row for every failure the console words", () => {
    const missing = MAIL_FAILURES.map((code) => heading(text(words, `mail.failure.${code}`))).filter(
      (name) => !table.includes(name),
    );
    expect(missing).toEqual([]);
  });

  it("has a row for the field problems that need explaining", () => {
    const missing = FIELD_PROBLEMS.map((code) => heading(text(words, `mail.problems.${code}`))).filter(
      (name) => !table.includes(name),
    );
    expect(missing).toEqual([]);
  });
});
