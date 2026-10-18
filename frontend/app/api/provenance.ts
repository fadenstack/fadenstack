/**
 * Provenance module status (`/api/admin/provenance/status`): whether marking
 * AI-generated content is switched on, and what the engine says about itself
 * -- the contract it speaks, its version, what it can do, its plugins.
 *
 * Switching the module on and off goes through `servicesApi` like every
 * module; switching it off needs `confirm: true`.
 */
import { requester } from "./http";

const request = requester("/api/admin/provenance");

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
