/**
 * The Fadenstack apps offered to people (`/api/apps`, anyone signed in): the
 * desktop agent, the Office add-ins, the Nextcloud app, the Chrome extension.
 * What an administrator offers on the Updates page shows here. Texts in the
 * answer are already in the requested language.
 */
import { query, requester } from "./http";
import type { AppConnect, AppRelease } from "./updates";

const request = requester("/api");

export interface OfferedApp {
  id: string;
  name: string;
  description: string;
  page: string;
  store: string;
  connect: AppConnect | null;
  agents: Record<string, string>;
  latest: AppRelease | null;
  supported_until: string | null;
}

export interface OfferedApps {
  /** This server's address, as people enter it in an app. */
  server: string;
  vendor_links: boolean;
  apps: OfferedApp[];
}

export const appsApi = {
  offered(lang: string) {
    return request<OfferedApps>(`/apps${query({ lang })}`);
  },
};
