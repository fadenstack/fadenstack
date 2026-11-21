/**
 * The model a chat starts with when its project has a default one.
 *
 * A project's ``model_alias`` is the model its chats start with; empty means
 * the chat's own choice, which the page keeps as it is.
 */
import type { ChatProject, ChatSession } from "~/api/chatTypes";

export function projectModelOf(
  session: Pick<ChatSession, "project_id"> | undefined,
  projects: Pick<ChatProject, "id" | "model_alias">[],
): string | null {
  if (!session?.project_id) return null;
  return projects.find((p) => p.id === session.project_id)?.model_alias || null;
}
