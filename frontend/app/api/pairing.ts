/**
 * Approving an app's sign-in in the console (pairing, RFC 8628): look a code up, then approve or deny it
 * (`/api/agent-tokens/pairings/{lookup,approve,deny}`).
 *
 * The code travels in the body of a POST, never in an address, so no access log has it (ASVS 5.0 14.2.1); no
 * error raised here carries it either. The cookie signs the request in, and the backend's CSRF check (the
 * request's Origin) covers these POSTs as every other of the console. Refusals:
 *
 * - 404 `unknown_code`: wrong or expired (counts as a wrong try);
 * - 409 `already_decided`: approved or denied already;
 * - 429 `too_many_attempts`: too many wrong codes, `Retry-After` says for how long (nginx's own 429 too);
 * - 403 `agent_not_available`: the agent is unknown, retired or not granted to this person (approve only);
 * - 401: the console sign-in ended.
 */
import { readError } from "./http";

/** What the app says it is: its own words, shown as such. */
export interface PairingClient {
  name: string | null;
  version: string | null;
  os: string | null;
}

/** What asks to be signed in, for the person to check before approving. */
export interface PairingLookup {
  /** `name` is null when the agent is not available to the person (unknown, retired or not granted alike). */
  agent: { slug: string; name: string | null };
  /** Whether the person may use the agent; approving answers 403 otherwise. */
  available: boolean;
  device_label: string;
  client: PairingClient;
  requester_ip: string | null;
  /** Whether the app asked from the network this browser is in. */
  same_network: boolean;
  created_at: string;
  expires_at: string;
  /** How long the sign-in stays: idle, and at most. */
  sign_in_lasts: { idle_days: number; max_days: number };
}

/** A refused lookup, approval or denial. Its message names the status only, never the code. */
export class PairingRefused extends Error {
  constructor(
    readonly status: number,
    /** The backend's code (`unknown_code`, `already_decided`, `too_many_attempts`, `agent_not_available`). */
    readonly code: string | undefined,
    /** Seconds to wait (`Retry-After`), for a 429. */
    readonly retryAfter: number | null,
  ) {
    super(`The server refused the pairing step (${status}).`);
    this.name = "PairingRefused";
  }
}

type Step = "lookup" | "approve" | "deny";

async function post<T>(step: Step, code: string): Promise<T> {
  const res = await fetch(`/api/agent-tokens/pairings/${step}`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const wait = Number.parseInt(res.headers.get("Retry-After") ?? "", 10);
    const { code: reason } = await readError(res);
    throw new PairingRefused(res.status, reason, Number.isFinite(wait) && wait > 0 ? wait : null);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const pairingApi = {
  lookup: (code: string) => post<PairingLookup>("lookup", code),
  approve: (code: string) => post<void>("approve", code),
  deny: (code: string) => post<void>("deny", code),
};
