/**
 * Provenance module status (`/api/admin/provenance/status`): whether marking
 * AI-generated content is switched on, and what the engine says about itself
 * -- the contract it speaks, its version, what it can do, its plugins.
 *
 * Switching the module on and off goes through `servicesApi` like every
 * module; switching it off needs `confirm: true`.
 *
 * Checking content for marks: `/api/provenance` for everyone signed in with
 * `provenance:check`, and `/api/public/provenance` -- pictures and files only
 * -- when an administrator offers the public page (404 otherwise). Nothing
 * submitted is kept.
 */
import { json, requester } from "./http";

const request = requester("/api/admin/provenance");
const checkRequest = requester("/api/provenance");
const publicRequest = requester("/api/public/provenance");

/**
 * running: the engine answered. not_running: no answer (stopped, unreachable).
 * refused: it turned the service token down. unexpected: it answered with
 * something that is not its info.
 */
export type ProvenanceEngineState =
  | "running"
  | "not_running"
  | "refused"
  | "unexpected";

export interface ProvenanceStatus {
  /** The module's switch (the backend's setting). */
  enabled: boolean;
  state: ProvenanceEngineState;
  contract: string | null;
  /** The contract the backend speaks, e.g. `provenance/v1`. */
  expected_contract: string;
  contract_supported: boolean;
  engine_version: string | null;
  /** What the engine can do now, e.g. `{ images: false, receipts: true }`. */
  capabilities: Record<string, boolean>;
  plugins: string[];
}

/** The module's status; the backend answers even when the engine is down. */
export function fetchProvenanceStatus(): Promise<ProvenanceStatus> {
  return request<ProvenanceStatus>("/status");
}

// ── Checking content ─────────────────────────────────────────────────

/** ai-generated: a mark of ours holds; inconclusive: only weaker signs; no-mark-found: nothing. */
export type ContentVerdict = "ai-generated" | "no-mark-found" | "inconclusive";

export type FindingLayer = "watermark" | "metadata" | "fingerprint" | "label";

/** What one detection mechanism found (the engine's `VerifyFinding`). */
export interface ContentFinding {
  layer: FindingLayer;
  /** e.g. `c2pa`, `trustmark-Q`, `receipt-hash`. */
  method: string;
  /** Whether it points to AI generation. */
  found: boolean;
  /** The engine's sentence about it. */
  detail: string;
  record_id?: string | null;
  receipt_id?: string | null;
  /** The mechanism's own measure, e.g. a p-value. */
  score?: number | null;
}

/** The engine's `VerifyResult`, as the backend passes it on. */
export interface ContentCheckResult {
  contract?: string;
  verdict: ContentVerdict;
  findings: ContentFinding[];
  /** SHA-256 of what was checked, hex. */
  content_hash: string;
  /** The detection solution's identifier. */
  detector: string;
  checked_at: string;
  /** A JWS over the result without it, when asked for and the engine can sign. */
  signature?: string | null;
}

/** A text or a file (base64); `signed` asks for a result to download. */
export interface ContentCheckBody {
  text?: string | null;
  file?: string | null;
  media_type?: string | null;
  signed?: boolean;
}

export interface ContentCheckStatus {
  /** Whether the provenance module is on. */
  enabled: boolean;
  /** The largest content checked, in bytes. */
  max_bytes: number;
}

/** Whether content can be checked; 403 without `provenance:check`. */
export function fetchContentCheckStatus(): Promise<ContentCheckStatus> {
  return checkRequest<ContentCheckStatus>("/status");
}

/** Check a text or a file, signed in. */
export function checkContent(body: ContentCheckBody): Promise<ContentCheckResult> {
  return checkRequest<ContentCheckResult>("/verify", json("POST", body));
}

export interface PublicCheckInfo {
  /** Whom to ask for a text check; empty when none is named. */
  contact: string;
  max_bytes: number;
}

/** The public page's facts; an `ApiError` with status 404 when it is not offered. */
export function fetchPublicCheck(): Promise<PublicCheckInfo> {
  return publicRequest<PublicCheckInfo>("");
}

/** Check a picture or a file without an account; texts are refused (403 `text_not_public`). */
export function checkContentPublicly(body: ContentCheckBody): Promise<ContentCheckResult> {
  return publicRequest<ContentCheckResult>("/check", json("POST", body));
}
