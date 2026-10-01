/**
 * Line diffs, for showing what changed between two versions of a text.
 *
 * `diffLines` compares two texts (the agents' versions); `parseUnifiedDiff`
 * reads a diff the server made (the skills' versions). Both give the lines
 * `DiffView` renders.
 */

export type DiffLineKind = "same" | "added" | "removed" | "hunk";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

/** Above this many cells the LCS table is too big; the texts are shown as replaced wholesale. */
const MAX_CELLS = 4_000_000;

/** The lines of `after` against `before`, in order: unchanged, removed and added. */
export function diffLines(before: string, after: string): DiffLine[] {
  if (before === after) return before === "" ? [] : split(before).map((text) => ({ kind: "same", text }));
  const a = split(before);
  const b = split(after);
  if (a.length * b.length > MAX_CELLS) {
    return [
      ...a.map((text): DiffLine => ({ kind: "removed", text })),
      ...b.map((text): DiffLine => ({ kind: "added", text })),
    ];
  }
  // lcs[i][j]: the longest common subsequence of a[i..] and b[j..].
  const lcs = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "removed", text: a[i++] });
    } else {
      out.push({ kind: "added", text: b[j++] });
    }
  }
  while (i < a.length) out.push({ kind: "removed", text: a[i++] });
  while (j < b.length) out.push({ kind: "added", text: b[j++] });
  return out;
}

/** A unified diff's lines (`+`, `-`, `@@`); its file headers are left out. */
export function parseUnifiedDiff(text: string): DiffLine[] {
  if (text.trim() === "") return [];
  return text
    .split("\n")
    .filter((line) => !line.startsWith("+++") && !line.startsWith("---"))
    .map((line): DiffLine => {
      if (line.startsWith("@@")) return { kind: "hunk", text: line };
      if (line.startsWith("+")) return { kind: "added", text: line.slice(1) };
      if (line.startsWith("-")) return { kind: "removed", text: line.slice(1) };
      return { kind: "same", text: line.startsWith(" ") ? line.slice(1) : line };
    });
}

/** Whether a diff changes anything. */
export function hasChanges(lines: DiffLine[]): boolean {
  return lines.some((line) => line.kind === "added" || line.kind === "removed");
}

function split(text: string): string[] {
  return text === "" ? [] : text.replace(/\r\n/g, "\n").split("\n");
}
