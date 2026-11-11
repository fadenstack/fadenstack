/**
 * The code an app shows when it asks to be signed in (pairing, RFC 8628), as the console's `/pair` page takes it.
 *
 * A code is 8 letters from BCDFGHJKLMNPQRSTVWXZ (no vowels, no digits), read as `XXXX-XXXX`. The app's link
 * carries it in the address's fragment (`/pair#code=XXXX-XXXX`), which browsers never send: no access log on the
 * way has it (ASVS 5.0 14.2.1). The page takes it from there and drops it from the address bar at once. To bring
 * it through the sign-in -- a password, or a provider (SSO) and back, which ends on `/login` without a target --
 * it is kept in this tab's sessionStorage for 10 minutes at most (as long as a code works), never in a query
 * string; the page takes it out again as soon as it shows it. It is never logged or sent anywhere else.
 */

/** The console page that approves an app's sign-in. */
export const PAIR_PATH = "/pair";
export const CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ";
export const CODE_LENGTH = 8;

const STORAGE_KEY = "faden-pair-code";
/** A code works for 10 minutes: one kept longer is of no use. */
const KEEP_MS = 10 * 60 * 1000;

/** The code's letters in what was typed: upper case, only letters a code has, at most 8. */
export function codeLetters(typed: string): string {
  let letters = "";
  for (const ch of typed.toUpperCase()) {
    if (letters.length === CODE_LENGTH) break;
    if (CODE_ALPHABET.includes(ch)) letters += ch;
  }
  return letters;
}

/** What the code field shows: the letters, with the dash after the fourth once a fifth follows. */
export function formatCode(typed: string): string {
  const letters = codeLetters(typed);
  return letters.length > 4 ? `${letters.slice(0, 4)}-${letters.slice(4)}` : letters;
}

/** Whether all 8 letters are there. */
export function isCompleteCode(typed: string): boolean {
  return codeLetters(typed).length === CODE_LENGTH;
}

/**
 * The code in the address's fragment (`#code=XXXX-XXXX`), formatted; it is dropped from the address bar (and so
 * from the history) at once. Null when the fragment has no code.
 */
export function takeCodeFromAddress(): string | null {
  const { hash, pathname, search } = window.location;
  if (!hash) return null;
  const code = new URLSearchParams(hash.slice(1)).get("code");
  if (code === null) return null;
  // The router's own state stays; only the fragment goes.
  window.history.replaceState(window.history.state, "", `${pathname}${search}`);
  return formatCode(code);
}

/** Keep a complete code in this tab for the way through the sign-in page. */
export function keepCode(code: string): void {
  if (!isCompleteCode(code)) return;
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ code: formatCode(code), at: Date.now() }));
  } catch {
    // Storage blocked: the person types the code after signing in.
  }
}

/** The code kept in this tab in the last 10 minutes; null when there is none (an older one is dropped). */
export function keptCode(): string | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const kept = JSON.parse(raw) as { code?: unknown; at?: unknown };
    if (
      typeof kept.code === "string" &&
      typeof kept.at === "number" &&
      Date.now() - kept.at < KEEP_MS &&
      isCompleteCode(kept.code)
    ) {
      return formatCode(kept.code);
    }
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage blocked, or no window (the server's render): nothing kept.
  }
  return null;
}

/** Forget the kept code. */
export function dropKeptCode(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage blocked: nothing was kept.
  }
}

/**
 * Where the sign-in page goes when its address names no target: back to `/pair` while an app's sign-in waits in
 * this tab. A sign-in with a provider (SSO) comes back to `/login` without one.
 */
export function signInTargetWhenNoneGiven(otherwise: string): string {
  return keptCode() ? PAIR_PATH : otherwise;
}
