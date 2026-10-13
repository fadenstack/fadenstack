/**
 * The user's language, kept on the server: picked once, in the console or in any agent's
 * settings, and every client of theirs follows it.
 *
 * The console keeps it in the user's preferences (`language`); the gateway hands the same
 * value to the SDK panels in their agent profile, and keeps what a user picks there.
 */

import i18n from "~/i18n";
import { listLanguages } from "~/api/i18n";
import { preferences } from "~/api/preferences";

/** The console's own name for a language: its primary subtag, as `load: "languageOnly"` loads it. */
export function consoleLanguage(tag: string): string {
  return tag.split("-")[0].toLowerCase();
}

/** Show the console in a language: loads its texts first, so nothing flashes in the old one. */
export async function showLanguage(code: string, namespaces: string[] = ["common"]): Promise<void> {
  await i18n.reloadResources([code], namespaces);
  await i18n.changeLanguage(code);
}

/** The user picked a language in the console: show it, and keep it for every client of theirs. */
export function pickLanguage(code: string, namespaces?: string[]): void {
  void showLanguage(code, namespaces);
  // The console still shows it when it cannot be kept now; the next pick tries again.
  void preferences.update({ language: code }).catch(() => undefined);
}

/**
 * Once signed in (never before: a refused read signs the console out): the language the user
 * picked anywhere wins over this browser's, when the console has texts for it. Returns the
 * language it switched to, or null.
 */
export async function applyUserLanguage(namespaces?: string[]): Promise<string | null> {
  const saved = await preferences
    .get()
    .then((r) => r.preferences.language ?? null)
    .catch(() => null);
  if (!saved) return null;
  const code = consoleLanguage(saved);
  if (consoleLanguage(i18n.resolvedLanguage || i18n.language || "en") === code) return null;
  // A language picked in an agent the console has no texts for (Italian, say) leaves it as it is.
  const available = await listLanguages().catch(() => []);
  if (!available.some((l) => l.code === code)) return null;
  await showLanguage(code, namespaces);
  return code;
}
