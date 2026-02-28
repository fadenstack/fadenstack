/**
 * Services manifest API client — discovers which optional modules
 * (RAG, PII, Auth, ...) are available on the backend.
 *
 * Endpoint: GET /api/admin/services
 */

const BASE = "/api/admin";

export interface ServiceInfo {
  name: string;
  display_name: string;
  description: string;
  enabled: boolean;
  status: "disabled" | "configured" | "healthy" | "unhealthy";
}

export interface ServicesManifest {
  services: ServiceInfo[];
}

async function assertOk(res: Response): Promise<void> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
}

export const servicesApi = {
  /**
   * Fetch the full services manifest.
   * The result includes every known optional module with its enabled
   * flag and health status.
   */
  async list(): Promise<ServicesManifest> {
    const res = await fetch(`${BASE}/services`, {
      method: "GET",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<ServicesManifest>;
  },
};
