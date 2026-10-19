/**
 * What the content check pages share: the files they take, a file's media
 * type and bytes as the engine wants them, sizes for people, the error codes
 * the backend answers with, and saving a signed result.
 *
 * Nothing here keeps what is checked: a file is read in the browser, sent,
 * and dropped with the page.
 */
import type { ContentCheckResult } from "~/api/provenance";

/** Signed in: pictures, Office documents, PDF and text files. */
export const ACCOUNT_FILE_TYPES = [
  "image/png",
  "image/jpeg",
  "image/webp",
  ".docx",
  ".pptx",
  ".xlsx",
  ".pdf",
  ".txt",
  ".md",
  ".html",
].join(",");

/** The public page: pictures, Office documents and PDF (texts are for people with an account). */
export const PUBLIC_FILE_TYPES = ["image/png", "image/jpeg", "image/webp", ".docx", ".pptx", ".xlsx", ".pdf"].join(",");

const BY_EXTENSION: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/markdown",
  markdown: "text/markdown",
  html: "text/html",
  htm: "text/html",
};

/** The file's media type: the browser's, or -- when it gives none (Markdown, often) -- its extension's. */
export function mediaTypeOf(file: { name: string; type: string }): string {
  if (file.type) return file.type.toLowerCase();
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return BY_EXTENSION[extension] ?? "application/octet-stream";
}

/** Whether the engine reads the file as a text (which the public page does not check). */
export function isTextType(mediaType: string): boolean {
  return mediaType.toLowerCase().startsWith("text/");
}

/** The file's bytes, base64 (without the `data:` prefix). */
export function readAsBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** How many bytes a text is in UTF-8, as the backend counts it. */
export function textBytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** A size for people: KB below a megabyte, MB above (1024-based, as the limit is). */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const mb = bytes / (1024 * 1024);
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`;
}

/**
 * A finding's measure for people: two significant digits, and very small ones (a watermark's p-value, often
 * 1e-9 or less) as a power of ten, as the finding's own text says it ("p = 6.4e-9"), not all seventeen digits.
 */
export function scoreLabel(score: number): string {
  if (score !== 0 && Math.abs(score) < 0.001) return score.toExponential(1);
  return String(Number(score.toPrecision(2)));
}

/** The error codes the backend and the engine answer with; others are shown with the server's message. */
export const KNOWN_ERRORS = new Set([
  "too_large",
  "busy",
  "unavailable",
  "timeout",
  "no_signing_key",
  "module_off",
  "rate_limited",
  "refused",
  "unexpected",
  "invalid",
  "not-base64",
  "nothing-to-check",
  "text_not_public",
]);

/** Save the signed result as a JSON file. */
export function saveResult(result: ContentCheckResult): void {
  const blob = new Blob([`${JSON.stringify(result, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `content-check-${result.content_hash.slice(0, 12)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
