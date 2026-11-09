/**
 * The second sign-in before Bypass is switched on (``/api/auth/step-up``). The browser's own cookie
 * (``faden_step_up``) binds the page to the app's request, so the code is sent once, in a body, never in a URL.
 */
const BASE = "/api/auth/step-up";

export type StepUpMethod = "password" | "oidc" | "saml" | "ldap";

export interface StepUpProvider {
  key: string;
  name: string;
  kind: "redirect" | "password";
  posts_back: boolean;
}

/** Where this browser's step-up stands, and what the page shows. */
export interface StepUpState {
  state: "sign_in" | "decide" | "approved" | "denied" | "expired";
  password: boolean;
  providers: StepUpProvider[];
  agent: string | null;
  device: string | null;
  longest_minutes: number | null;
  signed_in_as: string | null;
  method: StepUpMethod | null;
  until: string | null;
}

/** A refused request: ``code`` is the backend's (``wrong_credentials``, ``too_many_tries``, ...), never shown as it is. */
export class StepUpRefused extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly retryAfter: number | null = null,
  ) {
    super(code);
  }
}

async function request<T>(path: string, init: { method: "GET" | "POST"; body?: unknown }): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: init.method,
    credentials: "same-origin",
    headers: init.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as {
      detail?: { code?: unknown; retry_after?: unknown } | unknown;
    };
    const detail = body.detail as { code?: unknown; retry_after?: unknown } | undefined;
    const code = typeof detail?.code === "string" ? detail.code : "other";
    const wait = typeof detail?.retry_after === "number" ? detail.retry_after : null;
    throw new StepUpRefused(code, res.status, wait);
  }
  return (await res.json()) as T;
}

export const stepUpApi = {
  /** The link's code opens the step-up in this browser. */
  begin: (code: string) => request<StepUpState>("/begin", { method: "POST", body: { code } }),
  state: () => request<StepUpState>("", { method: "GET" }),
  password: (email: string, password: string) =>
    request<StepUpState>("/password", { method: "POST", body: { email, password } }),
  directory: (key: string, username: string, password: string) =>
    request<StepUpState>(`/directory/${encodeURIComponent(key)}`, { method: "POST", body: { username, password } }),
  /** Off to a sign-in provider that must authenticate the person anew: where to send the browser. */
  sso: async (key: string) =>
    (await request<{ redirect_url: string }>(`/sso/${encodeURIComponent(key)}`, { method: "POST", body: {} }))
      .redirect_url,
  approve: () => request<StepUpState>("/approve", { method: "POST", body: {} }),
  deny: () => request<StepUpState>("/deny", { method: "POST", body: {} }),
};
