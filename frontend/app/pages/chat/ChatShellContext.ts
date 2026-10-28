/**
 * What the chat page's shell knows that its views' bars need: who is signed
 * in, and -- on a phone -- the navigation button and panel. A context rather
 * than props: the welcome and conversation views both pass it through to
 * their top bar and composer, and a view rendered alone (a test) gets none.
 */
import { createContext, useContext } from "react";
import type { NavHand } from "~/lib/useNavHand";

/** The id of the chat's navigation panel, for the controls that open it. */
export const CHAT_NAV_ID = "chat-navigation";

export interface ChatPhoneNav {
  hand: NavHand;
  open: boolean;
  onOpen: () => void;
}

export interface ChatShell {
  userEmail: string;
  onProfile: () => void;
  onLogout: () => void;
  /** Open the user's skills page; no entry when skills are off. */
  onSkills?: () => void;
  /** Open the agents the user looks after; no entry without the right to. */
  onAgents?: () => void;
  /** Open the content check; no entry without the right to or while the provenance module is off. */
  onCheckContent?: () => void;
  /** Open the user's own usage page; everyone has it. */
  onUsage?: () => void;
  /** Set on a phone (below 600 px). */
  phoneNav: ChatPhoneNav | null;
}

export const ChatShellContext = createContext<ChatShell | null>(null);

export function useChatShell(): ChatShell | null {
  return useContext(ChatShellContext);
}
