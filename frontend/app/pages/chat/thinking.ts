/**
 * The composer's thinking levels: Auto, Off, Low, Medium, High (as the SDK panels offer them).
 *
 * A model's list says which of them it tells apart (``reasoning_levels``); Auto is always offered
 * and sends nothing, so the model decides. Off goes to the gateway as ``reasoning_effort: "none"``.
 */
import type { ModelAlias, ReasoningLevel } from "~/api/chatTypes";

export type ThinkingLevel = "auto" | ReasoningLevel;

export const THINKING_LEVELS: ThinkingLevel[] = ["auto", "off", "low", "medium", "high"];

export function isThinkingLevel(value: unknown): value is ThinkingLevel {
  return typeof value === "string" && (THINKING_LEVELS as string[]).includes(value);
}

/** The levels the model offers, Auto first; every level when the model's list does not say. */
export function offeredLevels(model: ModelAlias | null): ThinkingLevel[] {
  if (!model) return ["auto"];
  const levels = model.reasoning_levels;
  if (levels == null) return THINKING_LEVELS;
  return THINKING_LEVELS.filter((level) => level === "auto" || levels.includes(level));
}

/** Whether the pill shows: a model that offers nothing but Auto has nothing to choose. */
export function hasThinkingChoice(model: ModelAlias | null): boolean {
  return offeredLevels(model).length > 1;
}

/** The kept level when the model offers it, Auto otherwise. */
export function effectiveLevel(model: ModelAlias | null, kept: ThinkingLevel | undefined): ThinkingLevel {
  return kept && offeredLevels(model).includes(kept) ? kept : "auto";
}

/** What goes on the turn's body as ``reasoning_effort``; nothing for Auto. */
export function reasoningEffort(level: ThinkingLevel): string | undefined {
  if (level === "auto") return undefined;
  return level === "off" ? "none" : level;
}
