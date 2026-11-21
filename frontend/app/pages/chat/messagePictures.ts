/**
 * The pictures a message carries, read from its ``content_parts``.
 *
 * A message sent from this page holds the picture as a local part (an object
 * URL, with the file's name) until the page is loaded again; one the gateway
 * stored holds what the sender put in the request. Only a picture held in
 * the message itself (``data:image/...`` or this page's own ``blob:`` URL) is
 * shown: a link to another site is not fetched from here, as that would
 * tell that site who reads the chat.
 */
import { isPictureFile } from "~/api/chatTypes";

export interface MessagePicture {
  url: string;
  /** The file's name, when the part carries it. */
  name?: string;
}

const HELD_IN_MESSAGE = /^(data:image\/(png|jpe?g|gif|webp)[;,]|blob:)/i;

/** The pictures among a message's parts, in order. */
export function picturesOf(parts: unknown[] | null | undefined): MessagePicture[] {
  if (!Array.isArray(parts)) return [];
  const found: MessagePicture[] = [];
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const record = part as Record<string, unknown>;
    if (record.type !== "image_url" && record.type !== "input_image") continue;
    const ref = record.image_url;
    const url =
      typeof ref === "string"
        ? ref
        : ref && typeof ref === "object" && typeof (ref as Record<string, unknown>).url === "string"
          ? ((ref as Record<string, unknown>).url as string)
          : "";
    if (!HELD_IN_MESSAGE.test(url)) continue;
    found.push({ url, ...(typeof record.filename === "string" ? { name: record.filename } : {}) });
  }
  return found;
}

/** Local parts for the pictures among the files being sent, and the object URLs made for them. */
export function localPictureParts(files: File[] | undefined): { parts: unknown[]; urls: string[] } {
  const parts: unknown[] = [];
  const urls: string[] = [];
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return { parts, urls };
  for (const file of files ?? []) {
    if (!isPictureFile(file.name)) continue;
    const url = URL.createObjectURL(file);
    urls.push(url);
    parts.push({ type: "image_url", image_url: { url }, filename: file.name });
  }
  return { parts, urls };
}

/**
 * A list of messages as the gateway has them, with the pictures this page held
 * for the ones it sent (the gateway's copy has no parts for a picture sent as
 * a file): the thumbnail stays when the list is read again.
 */
export function withPictures<T extends { id: string; role: string; content: string; content_parts: unknown[] | null }>(
  before: T[],
  after: T[],
): T[] {
  const held = before.filter((m) => m.role === "user" && picturesOf(m.content_parts).length > 0);
  if (held.length === 0) return after;
  const claimed = new Set<string>();
  return after.map((m) => {
    if (m.role !== "user" || picturesOf(m.content_parts).length > 0) return m;
    const same =
      held.find((h) => h.id === m.id) ??
      held.find((h) => h.id.startsWith("tmp-") && !claimed.has(h.id) && h.content === m.content);
    if (!same) return m;
    claimed.add(same.id);
    return { ...m, content_parts: same.content_parts };
  });
}
