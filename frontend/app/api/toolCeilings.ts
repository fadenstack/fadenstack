/**
 * Tool mode limits (`/api/ee/tool-ceilings`, for administrators): the most a chat's tool mode may be, for
 * everyone, a team, an agent or a person; of those that apply to a chat the narrowest wins. Offered while the
 * server lists the feature {@link TOOL_MODE_CEILINGS} (`GET /api/about`).
 */
import { json, requester, segment } from "./http";

const request = requester("/api/ee/tool-ceilings");

/** The feature the tab shows for. */
export const TOOL_MODE_CEILINGS = "tool_mode_ceilings";
/** The target of the limit for everyone. */
export const EVERYONE = "default";

export type CeilingScope = "tenant" | "team" | "agent" | "user";
export type ToolMode = "off" | "read_only" | "ask" | "auto";
/** From the narrowest to the widest. */
export const TOOL_MODES: ToolMode[] = ["off", "read_only", "ask", "auto"];

export interface ToolCeiling {
  scope: CeilingScope;
  /** The team's, agent's or person's id; {@link EVERYONE} for everyone. */
  target_id: string;
  /** The team's or agent's name, the person's e-mail; null for everyone. */
  label: string | null;
  mode: ToolMode;
  updated_at: string;
  updated_by: string | null;
}

const at = (scope: CeilingScope, target: string) => `/${segment(scope)}/${segment(target)}`;

export const toolCeilingsApi = {
  list: () => request<ToolCeiling[]>(""),
  set: (scope: CeilingScope, target: string, mode: ToolMode) =>
    request<ToolCeiling>(at(scope, target), json("PUT", { mode })),
  remove: (scope: CeilingScope, target: string) => request<void>(at(scope, target), json("DELETE")),
};
