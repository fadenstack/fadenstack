/**
 * Has the console been upgraded under this page?
 *
 * Route discovery is "initial" (react-router.config.ts): a page gets the whole
 * route table once and never asks the server again, so a tab opened before an
 * upgrade went on running the old console -- old screens, old translations --
 * until someone forced a reload. It compares its own build with the one the
 * server runs (GET /_build, routes/build.ts).
 */

/** The build this page runs; `null` outside a React Router page (tests, the dev server's first paint). */
export function runningBuild(): string | null {
  if (typeof window === "undefined") return null;
  const manifest = (window as unknown as { __reactRouterManifest?: { version?: unknown } }).__reactRouterManifest;
  return typeof manifest?.version === "string" ? manifest.version : null;
}

/**
 * Text typed into the page and not yet sent: a reload would lose it.
 *
 * Nothing here keeps drafts (the chat composer included), so a page holding
 * typed text is only ever reloaded by the user's own hand.
 */
export function hasTypedText(root: ParentNode = document): boolean {
  const fields = root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
    'textarea, input:not([type]), input[type="text"], input[type="search"], input[type="email"], input[type="url"], input[type="number"]',
  );
  return Array.from(fields).some((field) => !field.disabled && !field.readOnly && field.value.trim() !== "");
}

/** Whether the server runs another build than this page; `false` whenever it cannot tell. */
export async function serverRunsNewerBuild(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  const running = runningBuild();
  if (!running) return false;
  try {
    const response = await fetchImpl("/_build", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return false;
    const body = (await response.json()) as { version?: unknown };
    return typeof body.version === "string" && body.version !== "" && body.version !== running;
  } catch {
    return false;
  }
}
