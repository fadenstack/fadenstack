/**
 * Guides: the console tour and the how-to guides, as data.
 *
 * A guide is a list of steps, each pointing at one element on a page
 * (`target`, usually `[data-tour-id='...']`). Its texts live in the "tour"
 * namespace under `guides.<guide>.<step>.title` / `.body`, so adding a guide
 * means adding a JSON file here and its strings in every language.
 *
 * Step kinds:
 *  - an ordinary step shows Next;
 *  - an `action` step asks the person to do something (click the highlighted
 *    button, fill in a dialog and press its own Next). It shows no Next of its
 *    own and moves on when the next step's target appears;
 *  - a `dialog` step points into a dialog or drawer: no dimming overlay, so the
 *    dialog stays usable while the tooltip explains it.
 */

import { preferences } from "~/api/preferences";

import consoleTour from "~/data/guides/console-tour.json";
import addMachine from "~/data/guides/add-machine.json";
import createCluster from "~/data/guides/create-cluster.json";
import hostModel from "~/data/guides/host-model.json";
import connectProvider from "~/data/guides/connect-provider.json";
import invitePeople from "~/data/guides/invite-people.json";
import createApiToken from "~/data/guides/create-api-token.json";

export type GuidePlacement = "top" | "bottom" | "left" | "right" | "auto" | "center";

export interface GuideStepDef {
  /** Step id: its texts are `guides.<guide>.<id>.title` and `.body`. */
  id: string;
  /** CSS selector of the element the step points at. */
  target: string;
  placement?: GuidePlacement;
  /** Go to this route before showing the step. */
  route?: string;
  /** The person acts; the step moves on when the next step's target appears. */
  action?: boolean;
  /** The target sits in a dialog or drawer: no overlay. */
  dialog?: boolean;
  /** How long to wait for the target to appear, in ms (it may be in a dialog still opening). */
  waitMs?: number;
  /** Shown only when this module is on / for superusers. */
  requires?: { module?: string; superuser?: boolean };
}

export interface GuideDef {
  id: string;
  /** "tour" shows the console; "howto" walks one task. */
  kind: "tour" | "howto";
  version: number;
  /** MUI icon name, for the guides list. */
  icon: string;
  /** Where the guide starts. */
  route: string;
  requires?: { module?: string; superuser?: boolean };
  steps: GuideStepDef[];
}

const GUIDES: GuideDef[] = [
  consoleTour,
  addMachine,
  createCluster,
  hostModel,
  connectProvider,
  invitePeople,
  createApiToken,
] as GuideDef[];

export interface GuideContext {
  isModuleEnabled: (module: string) => boolean;
  isSuperuser: boolean;
}

function allowed(requires: GuideDef["requires"], ctx: GuideContext): boolean {
  if (requires?.module && !ctx.isModuleEnabled(requires.module)) return false;
  if (requires?.superuser && !ctx.isSuperuser) return false;
  return true;
}

export function listGuides(): GuideDef[] {
  return GUIDES;
}

export function getGuide(id: string): GuideDef | undefined {
  return GUIDES.find((g) => g.id === id);
}

/** The how-to guides this person can follow, in catalogue order. */
export function availableHowTos(ctx: GuideContext): GuideDef[] {
  return GUIDES.filter((g) => g.kind === "howto" && allowed(g.requires, ctx));
}

/** The guide's steps this person sees. */
export function eligibleSteps(guide: GuideDef, ctx: GuideContext): GuideStepDef[] {
  return guide.steps.filter((s) => allowed(s.requires, ctx));
}

export function guideTitleKey(id: string): string {
  return `guides.${id}.title`;
}

export function guideDescriptionKey(id: string): string {
  return `guides.${id}.description`;
}

export function stepTitleKey(guideId: string, stepId: string): string {
  return `guides.${guideId}.${stepId}.title`;
}

export function stepBodyKey(guideId: string, stepId: string): string {
  return `guides.${guideId}.${stepId}.body`;
}

/** Every text key the guides use, for tests. */
export function guideTextKeys(): string[] {
  return GUIDES.flatMap((g) => [
    guideTitleKey(g.id),
    guideDescriptionKey(g.id),
    ...g.steps.flatMap((s) => [stepTitleKey(g.id, s.id), stepBodyKey(g.id, s.id)]),
  ]);
}

// ── Progress ─────────────────────────────────────────────────────────

const progressKey = (id: string) => `guide_${id}`;

/** The progress keys of every guide, for resetting them. */
export function guideProgressKeys(): string[] {
  return GUIDES.map((g) => progressKey(g.id));
}

/** Records that a guide was finished, so the getting-started list can tick it. */
export async function markGuideCompleted(id: string, version: number): Promise<void> {
  await preferences.update({
    tour_progress: {
      [progressKey(id)]: {
        version,
        current_step: -1,
        completed: true,
        skipped_steps: [],
        completed_at: new Date().toISOString(),
      },
    },
  });
}

export function isGuideCompleted(
  progress: Record<string, { completed?: boolean } | null> | null | undefined,
  id: string,
): boolean {
  return Boolean(progress?.[progressKey(id)]?.completed);
}
