/**
 * Into the maintenance page when the platform goes down for maintenance.
 *
 * While Fadenstack is updated (`faden upgrade`) or switched to maintenance,
 * nginx answers every API call with 503 and `X-Faden-Maintenance:
 * maintenance` (shared/nginx/locations.conf), and every page with its
 * maintenance page, which reloads itself once the platform is back -- on the
 * new version. A console left open would instead show its calls failing one
 * by one; it reloads into that page at its first refused call.
 *
 * Only for "maintenance": a service that is merely down answers
 * "unavailable", and reloading for that would loop while it stays down.
 */
let installed = false;

export function followMaintenance(reload: () => void = () => window.location.reload()): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const original = window.fetch.bind(window);
  let reloading = false;
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const response = await original(...args);
    if (
      !reloading &&
      response.status === 503 &&
      response.headers.get("x-faden-maintenance") === "maintenance"
    ) {
      reloading = true;
      reload();
    }
    return response;
  };
}

/** For tests: forget that the hook is in place (the caller restores `fetch`). */
export function resetFollowMaintenance(): void {
  installed = false;
}
