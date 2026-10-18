/**
 * Services manifest API client — discovers which optional modules
 * (RAG, PII, Auth, ...) are available on the backend and provides
 * enable / disable lifecycle operations.
 *
 * Endpoint: GET  /api/admin/services
 *           PUT  /api/admin/services/:name/enable
 *           PUT  /api/admin/services/:name/disable
 */

import { readError } from "./http";

const BASE = "/api/admin";

export interface ContainerState {
  name: string;
  state: string; // "running" | "exited" | "paused" | "not_found" | …
}

export interface ServiceInfo {
  name: string;
  display_name: string;
  description: string;
  /** Whether the settings flag is set (env var configured). */
  configured: boolean;
  /** Whether module containers are actually running. */
  enabled: boolean;
  status: "disabled" | "configured" | "healthy" | "unhealthy";
  containers?: ContainerState[];
  /** Module type: "container" (Docker-managed) or "plugin" (in-process). */
  module_type?: "container" | "plugin";
  /** Whether this is an enterprise-only module. */
  enterprise?: boolean;
  /**
   * Set when switching the module off needs an explicit confirmation: what
   * is lost (provenance: content is no longer marked). The backend refuses
   * `disable` without `confirm: true`.
   */
  disable_warning?: string | null;
}

export interface ServicesManifest {
  services: ServiceInfo[];
}

export interface ModuleActionResult {
  module: string;
  action: "enable" | "disable";
  started?: string[];
  stopped?: string[];
  errors: string[];
}

async function assertOk(res: Response): Promise<void> {
  // The server's own message, and its code (`confirmation_required`).
  if (!res.ok) throw await readError(res);
}

export const servicesApi = {
  /**
   * Fetch the full services manifest.
   * The result includes every known optional module with its enabled
   * flag, health status, and container states.
   */
  async list(): Promise<ServicesManifest> {
    const res = await fetch(`${BASE}/services`, {
      method: "GET",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<ServicesManifest>;
  },

  /** Start all containers belonging to the named module. */
  async enable(name: string): Promise<ModuleActionResult> {
    const res = await fetch(
      `${BASE}/services/${encodeURIComponent(name)}/enable`,
      {
        method: "PUT",
        credentials: "include",
      },
    );
    await assertOk(res);
    return res.json() as Promise<ModuleActionResult>;
  },

  /**
   * Stop all containers belonging to the named module. A module with a
   * `disable_warning` is switched off only with `confirm: true`, which the
   * caller sends after the person confirmed.
   */
  async disable(
    name: string,
    options: { confirm?: boolean } = {},
  ): Promise<ModuleActionResult> {
    const res = await fetch(
      `${BASE}/services/${encodeURIComponent(name)}/disable`,
      {
        method: "PUT",
        credentials: "include",
        ...(options.confirm
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ confirm: true }),
            }
          : {}),
      },
    );
    await assertOk(res);
    return res.json() as Promise<ModuleActionResult>;
  },

  /**
   * Fetch the last `tail` log lines for a specific container within a
   * module.  Returns plain text.
   */
  async containerLogs(
    module: string,
    containerName: string,
    tail = 200,
  ): Promise<string> {
    const res = await fetch(
      `${BASE}/services/${encodeURIComponent(module)}/logs/${encodeURIComponent(containerName)}?tail=${tail}`,
      { method: "GET", credentials: "include" },
    );
    await assertOk(res);
    return res.text();
  },
};
