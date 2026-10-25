export interface AuthUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
}

/** What the sign-in page may offer (``GET /api/auth/options``). */
export interface SignInOptions {
  /** A forgotten password is reset by mail; otherwise an administrator sets a new one. */
  password_reset: boolean;
}

const ME_CACHE_TTL_MS = 30_000;

let meCache: { value: AuthUser; expiresAt: number } | null = null;
let meInFlight: Promise<AuthUser> | null = null;

function clearMeCache(): void {
  meCache = null;
  meInFlight = null;
}

async function assertOk(res: Response): Promise<void> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
}

/**
 * A refused password sign-in: ``code`` is the backend's (``LOGIN_BAD_CREDENTIALS``,
 * ``LOGIN_USE_SIGN_IN_PROVIDER``, ...), or ``TOO_MANY`` when the server is rate-limiting, or ``OTHER``.
 */
export class LoginRefused extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

/** A refused reset by an administrator's link: ``expired``, or the password rule's words. */
export class ResetRefused extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}

async function refusal(res: Response): Promise<LoginRefused> {
  if (res.status === 429) return new LoginRefused("TOO_MANY");
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return new LoginRefused(typeof body.detail === "string" ? body.detail : "OTHER");
}

export const auth = {
  async login(username: string, password: string): Promise<void> {
    const body = new URLSearchParams({ username, password });
    const res = await fetch("/api/auth/cookie/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (res.status === 400 || res.status === 429) throw await refusal(res);
    await assertOk(res);
    clearMeCache();
  },

  async logout(): Promise<void> {
    const res = await fetch("/api/auth/cookie/logout", {
      method: "POST",
      credentials: "include",
    });
    await assertOk(res);
    clearMeCache();
  },

  async me(): Promise<AuthUser> {
    const now = Date.now();
    if (meCache && meCache.expiresAt > now) {
      return meCache.value;
    }
    if (meInFlight) {
      return meInFlight;
    }

    meInFlight = (async () => {
      const res = await fetch("/api/users/me", {
        method: "GET",
        credentials: "include",
      });
      await assertOk(res);
      const user = (await res.json()) as AuthUser;
      meCache = { value: user, expiresAt: Date.now() + ME_CACHE_TTL_MS };
      return user;
    })();

    try {
      return await meInFlight;
    } finally {
      meInFlight = null;
    }
  },

  async meFresh(): Promise<AuthUser> {
    clearMeCache();
    const res = await fetch("/api/users/me", {
      method: "GET",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<AuthUser>;
  },

  async devLogin(): Promise<void> {
    const res = await fetch("/api/auth/dev-login", {
      method: "POST",
      credentials: "include",
    });
    await assertOk(res);
    clearMeCache();
  },

  /**
   * What the sign-in page may offer: a reset of a forgotten password by mail only while this server can send
   * mail. Unknown (an older server, no answer) reads as offered, as before.
   */
  async options(): Promise<SignInOptions> {
    const res = await fetch("/api/auth/options", { credentials: "include" });
    if (!res.ok) return { password_reset: true };
    return (await res.json()) as SignInOptions;
  },

  async forgotPassword(email: string): Promise<void> {
    const res = await fetch("/api/auth/forgot-password", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (res.status !== 202) {
      await assertOk(res);
    }
  },

  /** Whose password a link from an administrator sets; ``null`` once it no longer works. Changes nothing. */
  async resetLinkPreview(token: string): Promise<{ email: string; expires_at: string } | null> {
    const res = await fetch("/api/auth/reset-link/preview", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.status === 404) return null;
    await assertOk(res);
    return (await res.json()) as { email: string; expires_at: string };
  },

  /**
   * Set a new password with a link from an administrator, once; nobody is signed in. Throws
   * ``ResetRefused("expired")``, or ``ResetRefused`` with the password rule's words.
   */
  async resetWithLink(token: string, password: string): Promise<void> {
    const res = await fetch("/api/auth/reset-link/use", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    });
    if (res.ok) return;
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new ResetRefused(typeof body.detail === "string" ? body.detail : `status_${res.status}`);
  },

  async resetPassword(token: string, password: string): Promise<void> {
    const res = await fetch("/api/auth/reset-password", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    });
    await assertOk(res);
  },
};
