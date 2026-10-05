/**
 * Lets any page start a guide or open the getting-started list: the runner
 * lives in the admin layout, the buttons that start it on the pages.
 */
import { createContext, useContext } from "react";

export interface GuidesControl {
  startGuide: (guideId: string) => void;
  openGettingStarted: () => void;
}

const noop: GuidesControl = { startGuide: () => {}, openGettingStarted: () => {} };

export const GuidesContext = createContext<GuidesControl>(noop);

export function useGuides(): GuidesControl {
  return useContext(GuidesContext);
}

/** Fired when guide progress changed (a guide finished, the guides were reset). */
export const GUIDES_CHANGED_EVENT = "faden:guides-changed";

export function announceGuidesChanged(): void {
  window.dispatchEvent(new Event(GUIDES_CHANGED_EVENT));
}
