/**
 * The skills a chat uses: the ones the user can pick, the ones picked for
 * this chat, and the "create a skill" dialog.
 *
 * Picked skills are sent with every message of the chat (``skills`` in the
 * request) until they are taken off; the gateway resolves them among the
 * skills the user can see. They are kept per chat in this browser.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { listSkills, type SkillSummary } from "~/api/skills";

export interface ChatSkills {
  /** The Skills module is on and the user may use skills. */
  enabled: boolean;
  /** The user may create skills. */
  canCreate: boolean;
  /** The skills that can be picked: published, enabled, visible to the user. */
  usable: SkillSummary[];
  /** The slugs picked for this chat. */
  selected: string[];
  toggle: (slug: string) => void;
  remove: (slug: string) => void;
  /** Open the "create a skill" dialog (with what followed ``/skill``). */
  openCreate: (guidance?: string) => void;
  refresh: () => void;
}

const OFF: ChatSkills = {
  enabled: false,
  canCreate: false,
  usable: [],
  selected: [],
  toggle: () => {},
  remove: () => {},
  openCreate: () => {},
  refresh: () => {},
};

const ChatSkillsContext = createContext<ChatSkills>(OFF);

export const ChatSkillsProvider = ChatSkillsContext.Provider;

/** The chat's skills; outside a provider, skills are off. */
export function useChatSkills(): ChatSkills {
  return useContext(ChatSkillsContext);
}

const STORAGE_PREFIX = "faden-chat-skills:";
/** The key for a chat not started yet. */
export const NEW_CHAT = "new";

function readStored(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeStored(key: string, slugs: string[]): void {
  try {
    if (slugs.length) window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(slugs));
    else window.localStorage.removeItem(STORAGE_PREFIX + key);
  } catch {
    // Storage may be unavailable (private windows); the pick still works for now.
  }
}

/** The state behind ``ChatSkillsProvider``, kept by the chat page. */
export function useChatSkillsState({
  sessionId,
  enabled,
  canCreate,
}: {
  sessionId: string | undefined;
  enabled: boolean;
  canCreate: boolean;
}) {
  const key = sessionId ?? NEW_CHAT;
  const [usable, setUsable] = useState<SkillSummary[]>([]);
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [createFor, setCreateFor] = useState<{ guidance?: string } | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    listSkills({ usable: true, limit: 500 })
      .then((list) => {
        if (!cancelled) setUsable(list.items);
      })
      .catch(() => {
        if (!cancelled) setUsable([]);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, version]);

  const selected = useMemo(() => {
    const slugs = picked[key] ?? readStored(key);
    // Only what can still be picked: a skill archived since is dropped.
    if (usable.length === 0) return slugs;
    const known = new Set(usable.map((s) => s.slug));
    return slugs.filter((s) => known.has(s));
  }, [picked, key, usable]);

  const set = useCallback(
    (slugs: string[]) => {
      setPicked((prev) => ({ ...prev, [key]: slugs }));
      writeStored(key, slugs);
    },
    [key],
  );

  /** A new chat keeps what was picked before its first message. */
  const adopt = useCallback((newSessionId: string) => {
    const slugs = readStored(NEW_CHAT);
    setPicked((prev) => ({ ...prev, [newSessionId]: prev[NEW_CHAT] ?? slugs, [NEW_CHAT]: [] }));
    writeStored(newSessionId, slugs);
    writeStored(NEW_CHAT, []);
  }, []);

  const value: ChatSkills = useMemo(
    () => ({
      enabled,
      canCreate,
      usable,
      selected,
      toggle: (slug) =>
        set(selected.includes(slug) ? selected.filter((s) => s !== slug) : [...selected, slug]),
      remove: (slug) => set(selected.filter((s) => s !== slug)),
      openCreate: (guidance) => setCreateFor({ guidance }),
      refresh: () => setVersion((v) => v + 1),
    }),
    [enabled, canCreate, usable, selected, set],
  );

  return {
    value,
    adopt,
    createFor,
    closeCreate: () => setCreateFor(null),
    /** After a skill is created: list it, and pick it when asked to. */
    created: (skill: SkillSummary, useNow: boolean) => {
      setCreateFor(null);
      setUsable((prev) => [skill, ...prev.filter((s) => s.id !== skill.id)]);
      if (useNow && !selected.includes(skill.slug)) set([...selected, skill.slug]);
    },
  };
}
