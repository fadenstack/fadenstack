/**
 * What the console offers for an environment depends on its driver: a Ray
 * cluster has a network plan, drains, several machines and scaling; a
 * standalone environment is one Mac running one model at a time, with none of those.
 */
export const RAY_DRIVER = "ray";
export const STANDALONE_DRIVER = "standalone";

export interface DriverUi {
  /** Network plan and Ray fabric. */
  network: boolean;
  /** Draining a machine out of a cluster. */
  drain: boolean;
  /** More than one machine. */
  multiNode: boolean;
  /** Changing the number of copies. */
  scaling: boolean;
}

/** An unknown driver (none loaded yet) is treated as Ray, as before this existed. */
export function driverUi(driver: string | null | undefined): DriverUi {
  if (driver === STANDALONE_DRIVER) return { network: false, drain: false, multiNode: false, scaling: false };
  return { network: true, drain: true, multiNode: true, scaling: true };
}

/** A node whose agent can serve models natively (a Mac). */
export function isNativeNode(capabilities: Record<string, unknown> | undefined | null): boolean {
  return Number(capabilities?.native_runtime ?? 0) > 0;
}
