/**
 * The chat page's list of chats, kept whole while it loads and changes.
 *
 * The list is fetched once when the page opens -- over a thousand chats take
 * a moment -- and a chat started before that fetch returned was replaced by
 * it: the new chat vanished from the sidebar, and its title, fetched after
 * the reply, had no entry to update, so the bar said "Untitled chat" until a
 * reload (UI sweep on the VM, 2026-09-26, a 242 ms reply).
 */
import type { ChatSession } from "~/api/chatTypes";

/** *fetched*, with the chats of *current* it does not have yet and the newer copy of each. */
export function mergeSessions(fetched: ChatSession[], current: ChatSession[]): ChatSession[] {
  const byId = new Map(current.map((s) => [s.id, s]));
  const fetchedIds = new Set(fetched.map((s) => s.id));
  const merged = fetched.map((s) => {
    const mine = byId.get(s.id);
    return mine && mine.updated_at > s.updated_at ? mine : s;
  });
  return [...current.filter((s) => !fetchedIds.has(s.id)), ...merged];
}

/** *current* with *updated* in it: replaced where it is, added first where it is not. */
export function withSession(current: ChatSession[], updated: ChatSession): ChatSession[] {
  return current.some((s) => s.id === updated.id)
    ? current.map((s) => (s.id === updated.id ? updated : s))
    : [updated, ...current];
}
