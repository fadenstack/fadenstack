/**
 * The one way the newer API modules talk to the backend: JSON in and out,
 * the session cookie, and an error that carries the server's own message.
 *
 * FastAPI answers errors in several shapes -- `{"detail": "..."}`, a list of
 * validation errors, `{"detail": {"code", "message"}}` -- and the gateway the
 * OpenAI one, `{"error": {"message"}}`. `ApiError` reads all of them, so a page
 * shows what the server said rather than a status code.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Detail = string | { msg?: unknown }[] | { code?: string; message?: string } | undefined;

function errorFields(body: { detail?: Detail; error?: { message?: string; code?: string } }): {
  message?: string;
  code?: string;
} {
  const { detail } = body;
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    return { message: detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ") };
  }
  if (detail && typeof detail === "object") return { message: detail.message, code: detail.code };
  if (body.error) return { message: body.error.message, code: body.error.code };
  return {};
}

/** The error a failed response stands for. */
export async function readError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => "");
  let parsed: { message?: string; code?: string } = {};
  try {
    parsed = errorFields(JSON.parse(text));
  } catch {
    // not JSON: the text is the message
  }
  return new ApiError(parsed.message || text || `${res.status} ${res.statusText}`, res.status, parsed.code);
}

/** The message of anything a request may throw. */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

/** A `request` bound to `base`: JSON bodies (or FormData), cookies, typed errors, 204 as undefined. */
export function requester(base: string): Request {
  return async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const isForm = init.body instanceof FormData;
    const res = await fetch(`${base}${path}`, {
      ...init,
      headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(init.headers ?? {}) },
      credentials: "include",
    });
    if (!res.ok) throw await readError(res);
    if (res.status === 204) return undefined as T;
    const type = res.headers.get("content-type") ?? "";
    return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
  };
}

/** `?a=1&b=x` from the defined values of `params` ("" when there are none). */
export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  const text = qs.toString();
  return text ? `?${text}` : "";
}

/** A path segment from a user-given value. */
export const segment = encodeURIComponent;

/** A JSON body for `init`. */
export function json(method: "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): RequestInit {
  return body === undefined ? { method } : { method, body: JSON.stringify(body) };
}
